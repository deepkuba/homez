import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Literal
from urllib.parse import parse_qs, urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    FeedbackEventRecord,
    ListingRecord,
    ListingSnapshotRecord,
    ReportDraftRecord,
    SourceRecord,
    WorkflowJobRecord,
)
from homefinder.catalog.profile_repository import SqlAlchemyBuyerProfileRepository
from homefinder.config import Environment, Settings
from homefinder.digest.delivery import DeliveryOutbox
from homefinder.digest.feedback import (
    FeedbackError,
    SqlAlchemyFeedbackService,
)
from homefinder.domain.profile import BuyerProfile
from homefinder.enrichment.environment import ManualCorrectionStore
from homefinder.operations.health import HealthRegistry, HealthState
from homefinder.operations.logging import setup_logging
from homefinder.sources.gmail import TokenError, read_secret_text
from homefinder.web.scraper_errors import (
    InvalidScraperErrorCursor,
    load_scraper_errors,
    render_scraper_error_dashboard,
    stream_scraper_errors,
)
from homefinder.workflow.models import ManualReviewRequired
from homefinder.workflow.service import WorkflowService


class CorrectionPayload(BaseModel):
    field: str
    value: str
    corrected_by: str
    reason: str


def create_app(
    settings: Settings | None = None,
    *,
    feedback_service: SqlAlchemyFeedbackService | None = None,
) -> FastAPI:
    application = FastAPI(title="Homefinder", docs_url=None, redoc_url=None)
    application.state.settings = settings or Settings()
    setup_logging(application.state.settings.log_level)
    engine = create_engine(application.state.settings.database_url.get_secret_value())
    sessions = sessionmaker(engine, expire_on_commit=False)
    application.state.sessions = sessions
    application.state.feedback_service = feedback_service or SqlAlchemyFeedbackService(
        sessions
    )
    application.state.correction_store = ManualCorrectionStore()
    application.state.health_registry = HealthRegistry()
    application.state.health_registry.update("application", HealthState.OK)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        return response

    @application.get("/health", include_in_schema=False)
    def health() -> dict[str, object]:
        snapshot = application.state.health_registry.snapshot()
        return {
            "status": snapshot.status,
            "components": {
                name: {
                    "state": component.state.value,
                    "checked_at": component.checked_at.isoformat(),
                    "detail": component.detail,
                }
                for name, component in snapshot.components.items()
            },
            "oldest_pending_job": snapshot.oldest_pending_job,
            "oldest_pending_at": snapshot.oldest_pending_at.isoformat()
            if snapshot.oldest_pending_at is not None
            else None,
        }

    @application.get("/feedback/offers", response_class=HTMLResponse)
    def offers(
        authorization: str | None = Header(default=None),
        feedback: Literal["all", "with_feedback", "without_feedback"] = "all",
        page: int = 1,
        report: Literal["queued"] | None = None,
    ) -> HTMLResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be positive")
        csrf = secrets.token_urlsafe(32)
        response = HTMLResponse(
            _render_offer_browser(
                application.state.sessions,
                feedback_filter=feedback,
                page=page,
                csrf_token=csrf,
                report_status=report,
            )
        )
        response.set_cookie(
            "homefinder_offers_csrf",
            csrf,
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=3600,
            path="/feedback/offers",
        )
        return response

    @application.post("/feedback/offers/report")
    async def manual_report(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> RedirectResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        body = await request.body()
        if len(body) > 1024:
            raise HTTPException(status_code=413, detail="request is too large")
        try:
            values = parse_qs(body.decode("utf-8"), strict_parsing=True)
            submitted_csrf = values["csrf_token"][0]
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as error:
            raise HTTPException(
                status_code=400, detail="invalid report request"
            ) from error
        expected_csrf = request.cookies.get("homefinder_offers_csrf", "")
        if not submitted_csrf or not hmac.compare_digest(submitted_csrf, expected_csrf):
            raise HTTPException(status_code=400, detail="invalid CSRF token")
        try:
            _queue_manual_report(
                application.state.settings,
                application.state.sessions,
                now=datetime.now(timezone.utc),
            )
        except ManualReviewRequired as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return RedirectResponse("/feedback/offers?report=queued", status_code=303)

    @application.post("/feedback/offers/{listing_id}")
    async def offer_feedback(
        listing_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> RedirectResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(status_code=413, detail="feedback request is too large")
        try:
            parsed_listing_id = UUID(listing_id)
            values = parse_qs(body.decode("utf-8"), strict_parsing=True)
            value = values["value"][0]
            submitted_csrf = values["csrf_token"][0]
            reason_code = values.get("reason_code", [None])[0]
            comment = values.get("comment", [None])[0]
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as error:
            raise HTTPException(
                status_code=400, detail="invalid feedback request"
            ) from error
        with application.state.sessions() as session:
            if session.get(ListingRecord, parsed_listing_id) is None:
                raise HTTPException(status_code=404, detail="listing not found")
        try:
            application.state.feedback_service.record_authenticated(
                method=request.method,
                csrf_token=submitted_csrf,
                expected_csrf=request.cookies.get("homefinder_offers_csrf", ""),
                value=value,
                now=datetime.now(timezone.utc),
                listing_id=listing_id,
                actor_hash=_actor_hash(application.state.settings, request),
                reason_code=reason_code,
                comment=comment,
            )
        except FeedbackError as error:
            status_code = 429 if "rate limit" in str(error) else 400
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        return RedirectResponse(
            "/feedback/offers?feedback=with_feedback", status_code=303
        )

    @application.get("/feedback/queue", response_class=HTMLResponse)
    def queue_status(
        authorization: str | None = Header(default=None),
    ) -> HTMLResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        return HTMLResponse(
            _render_queue_status(
                application.state.sessions, now=datetime.now(timezone.utc)
            )
        )

    @application.get("/feedback/scraper-errors", response_class=HTMLResponse)
    def scraper_errors(
        authorization: str | None = Header(default=None),
    ) -> HTMLResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        nonce = secrets.token_urlsafe(18)
        response = HTMLResponse(
            render_scraper_error_dashboard(
                application.state.sessions,
                nonce=nonce,
                now=datetime.now(timezone.utc),
            )
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"script-src 'nonce-{nonce}'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @application.get("/feedback/scraper-errors/history")
    def scraper_error_history(
        before: str,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        try:
            page = load_scraper_errors(application.state.sessions, before=before)
        except InvalidScraperErrorCursor as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return JSONResponse(
            {
                "items": [item.as_dict() for item in page.items],
                "next_cursor": page.next_cursor,
            }
        )

    @application.get("/feedback/scraper-errors/stream")
    def scraper_error_event_stream(
        after: str,
        authorization: str | None = Header(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        cursor = last_event_id or after
        try:
            load_scraper_errors(application.state.sessions, after=cursor, limit=1)
        except InvalidScraperErrorCursor as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return StreamingResponse(
            stream_scraper_errors(application.state.sessions, after=cursor),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @application.get("/feedback/settings", response_class=HTMLResponse)
    def profile_settings(
        authorization: str | None = Header(default=None),
        saved: Literal["1"] | None = None,
    ) -> HTMLResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        with application.state.sessions() as session:
            try:
                profile = SqlAlchemyBuyerProfileRepository(session).active()
            except LookupError as error:
                raise HTTPException(
                    status_code=409, detail="active buyer profile is required"
                ) from error
        csrf = secrets.token_urlsafe(32)
        response = HTMLResponse(
            _render_profile_settings(profile, csrf_token=csrf, saved=saved == "1")
        )
        response.set_cookie(
            "homefinder_settings_csrf",
            csrf,
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=3600,
            path="/feedback/settings",
        )
        return response

    @application.post("/feedback/settings")
    async def update_profile_settings(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> RedirectResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        body = await request.body()
        if len(body) > 16_384:
            raise HTTPException(status_code=413, detail="settings request is too large")
        try:
            values = parse_qs(
                body.decode("utf-8"), strict_parsing=True, keep_blank_values=True
            )
            submitted_csrf = _single_form_value(values, "csrf_token")
        except (ProfileSettingsError, UnicodeError, ValueError) as error:
            raise HTTPException(
                status_code=400, detail="invalid settings request"
            ) from error
        expected_csrf = request.cookies.get("homefinder_settings_csrf", "")
        if not submitted_csrf or not hmac.compare_digest(submitted_csrf, expected_csrf):
            raise HTTPException(status_code=400, detail="invalid CSRF token")
        now = datetime.now(timezone.utc)
        with application.state.sessions() as session:
            repository = SqlAlchemyBuyerProfileRepository(session)
            try:
                active = repository.active()
                profile = _profile_from_settings(
                    values,
                    active=active,
                    version=repository.next_version(),
                    effective_from=now.astimezone(ZoneInfo("Europe/Warsaw")).date(),
                )
                repository.add_approved(
                    profile, approved_by="buyer-settings", approved_at=now
                )
            except ProfileSettingsError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            except LookupError as error:
                raise HTTPException(
                    status_code=409, detail="active buyer profile is required"
                ) from error
            except ValueError as error:
                raise HTTPException(
                    status_code=409, detail="profile changed concurrently; retry"
                ) from error
        return RedirectResponse("/feedback/settings?saved=1", status_code=303)

    @application.get("/feedback/{report_id}/{listing_id}", response_class=HTMLResponse)
    def feedback_form(
        report_id: str,
        listing_id: str,
    ) -> HTMLResponse:
        csrf = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(18)
        form_action = f"/feedback/{escape(report_id)}/{escape(listing_id)}"
        body = (
            "<!doctype html><meta name=viewport content='width=device-width'>"
            "<title>Oceń ofertę</title><style>body{font-family:sans-serif;"
            "max-width:38rem;margin:2rem auto;padding:0 1rem}label{display:block;"
            "margin:.7rem 0}select,textarea,button{font:inherit;padding:.6rem;"
            "max-width:100%}textarea{width:100%;box-sizing:border-box}</style>"
            "<h1>Oceń ofertę</h1>"
            f"<form method=post action='{form_action}'>"
            "<input id=feedback-token type=hidden name=token>"
            f"<input type=hidden name=csrf_token value='{escape(csrf, quote=True)}'>"
            "<fieldset><legend>Jak oceniasz tę ofertę?</legend>"
            "<label><input type=radio name=value value=like required> "
            "Podoba mi się</label>"
            "<label><input type=radio name=value value=dislike> "
            "Nie podoba mi się</label>"
            "<label><input type=radio name=value value=save> Zapisz na później</label>"
            "</fieldset><div id=dislike-details hidden>"
            "<label>Dlaczego oferta Ci się nie podoba?"
            '<select id=reason-code name="reason_code">'
            "<option value=''>Wybierz powód</option>"
            '<option value="too_expensive">Za wysoka cena</option>'
            '<option value="too_high_admin_fee">Za wysoki czynsz</option>'
            '<option value="unsuitable_heating">Nieodpowiednie ogrzewanie</option>'
            '<option value="wrong_location">Nieodpowiednia lokalizacja</option>'
            '<option value="too_small">Za mały metraż</option>'
            '<option value="too_few_rooms">Za mało pokoi</option>'
            '<option value="poor_condition">Zły stan / za duży remont</option>'
            '<option value="bad_layout">Nieodpowiedni układ</option>'
            '<option value="commute">Zbyt długi dojazd</option>'
            '<option value="floor_or_no_elevator">Piętro lub brak windy</option>'
            '<option value="no_parking">Brak możliwości parkowania</option>'
            '<option value="legal_risk">Ryzyko prawne</option>'
            '<option value="other">Inny powód</option>'
            "</select></label></div>"
            "<label>Dodatkowy komentarz (opcjonalny)"
            '<textarea name="comment" maxlength=500 rows=4></textarea></label>'
            "<button type=submit>Zapisz ocenę</button></form>"
            f"<script nonce='{nonce}'>"
            "const t=location.hash.slice(1);"
            "document.getElementById('feedback-token').value=t;"
            "history.replaceState(null,'',location.pathname);"
            "const d=document.getElementById('dislike-details');"
            "const r=document.getElementById('reason-code');"
            "document.querySelectorAll('input[name=value]').forEach(e=>"
            "e.addEventListener('change',()=>{const x=e.value==='dislike'&&e.checked;"
            "d.hidden=!x;r.required=x;}));"
            "</script>"
        )
        response = HTMLResponse(body)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"script-src 'nonce-{nonce}'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        response.set_cookie(
            "homefinder_csrf",
            csrf,
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=600,
            path=f"/feedback/{report_id}/{listing_id}",
        )
        return response

    @application.post("/feedback/{report_id}/{listing_id}")
    async def feedback(
        report_id: str,
        listing_id: str,
        request: Request,
        x_csrf_token: str | None = Header(default=None),
    ) -> Response:
        service = application.state.feedback_service
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(status_code=413, detail="feedback request is too large")
        is_json = request.headers.get("content-type", "").startswith("application/json")
        try:
            if is_json:
                payload = await request.json()
                token = str(payload["token"])
                value = str(payload["value"])
                submitted_csrf = str(payload.get("csrf_token", ""))
                reason_code = str(payload.get("reason_code", "")) or None
                comment = str(payload.get("comment", "")) or None
            else:
                values = parse_qs(body.decode("utf-8"), strict_parsing=True)
                token = values["token"][0]
                value = values["value"][0]
                submitted_csrf = values["csrf_token"][0]
                reason_code = values.get("reason_code", [None])[0]
                comment = values.get("comment", [None])[0]
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as error:
            raise HTTPException(
                status_code=400, detail="invalid feedback request"
            ) from error
        csrf = request.cookies.get("homefinder_csrf")
        try:
            service.record(
                method=request.method,
                token=token,
                csrf_token=x_csrf_token or submitted_csrf,
                expected_csrf=csrf or "",
                value=value,
                now=datetime.now(timezone.utc),
                report_id=report_id,
                listing_id=listing_id,
                actor_hash=_actor_hash(application.state.settings, request),
                reason_code=reason_code,
                comment=comment,
            )
        except FeedbackError as error:
            detail = str(error)
            status_code = (
                409 if "used" in detail else 410 if "expired" in detail else 400
            )
            raise HTTPException(status_code=status_code, detail=detail) from error
        if is_json:
            return JSONResponse({"status": "recorded"})
        return HTMLResponse(
            "<!doctype html><title>Ocena zapisana</title>"
            "<p>Dziękujemy. Ocena została zapisana.</p>"
        )

    @application.post("/corrections/{property_id}")
    def correction(
        property_id: str,
        payload: CorrectionPayload,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _require_admin(application.state.settings, authorization)
        try:
            application.state.correction_store.record(
                property_id=property_id,
                field=payload.field,
                value=payload.value,
                corrected_by=payload.corrected_by,
                reason=payload.reason,
                corrected_at=datetime.now(timezone.utc),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"status": "recorded"}

    return application


def _actor_hash(settings: Settings, request: Request) -> str:
    host = request.client.host if request.client is not None else "unknown"
    if settings.feedback_rate_salt_file is None:
        if settings.environment is Environment.PRODUCTION:
            raise HTTPException(status_code=503, detail="feedback security unavailable")
        salt = "development-only"
    else:
        try:
            salt = read_secret_text(settings.feedback_rate_salt_file)
        except TokenError as error:
            raise HTTPException(
                status_code=503, detail="feedback security unavailable"
            ) from error
    return hashlib.sha256(f"{salt}:{host}".encode()).hexdigest()


def _require_admin(settings: Settings, authorization: str | None) -> None:
    if settings.admin_bearer_token_file is None:
        raise HTTPException(status_code=503, detail="administration unavailable")
    try:
        expected = read_secret_text(settings.admin_bearer_token_file)
    except TokenError as error:
        raise HTTPException(
            status_code=503, detail="administration unavailable"
        ) from error
    prefix = "Bearer "
    provided = (
        authorization[len(prefix) :]
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _require_offer_browser_admin(settings: Settings, authorization: str | None) -> None:
    challenge = {"WWW-Authenticate": 'Basic realm="Homez offers", charset="UTF-8"'}
    if authorization is None or not authorization.startswith("Basic "):
        raise HTTPException(
            status_code=401, detail="authentication required", headers=challenge
        )
    try:
        decoded = base64.b64decode(
            authorization.removeprefix("Basic "), validate=True
        ).decode("utf-8")
        username, separator, password = decoded.partition(":")
    except (binascii.Error, UnicodeError):
        raise HTTPException(
            status_code=401, detail="invalid credentials", headers=challenge
        ) from None
    if not separator or settings.admin_bearer_token_file is None:
        raise HTTPException(
            status_code=401, detail="invalid credentials", headers=challenge
        )
    try:
        expected = read_secret_text(settings.admin_bearer_token_file)
    except TokenError as error:
        raise HTTPException(
            status_code=503, detail="offer browser unavailable"
        ) from error
    valid = hmac.compare_digest(username, "homez") & hmac.compare_digest(
        password, expected
    )
    if not valid:
        raise HTTPException(
            status_code=401, detail="invalid credentials", headers=challenge
        )


class ProfileSettingsError(ValueError):
    pass


_HEATING_LABELS = {
    "district": "Miejskie / MPEC",
    "gas": "Gazowe",
    "electric": "Elektryczne",
    "heat_pump": "Pompa ciepła",
    "solid_fuel": "Paliwo stałe",
    "other": "Inne",
}
_SCORE_WEIGHT_FIELDS = (
    ("ready_to_move", "Gotowe do zamieszkania"),
    ("quiet", "Cicha okolica"),
    ("green_space", "Tereny zielone"),
    ("balcony", "Balkon"),
    ("separate_kitchen", "Oddzielna kuchnia"),
    ("small_building", "Mały budynek"),
    ("area_fit", "Dopasowanie metrażu"),
)


def _profile_from_settings(
    values: Mapping[str, list[str]],
    *,
    active: BuyerProfile,
    version: int,
    effective_from: date,
) -> BuyerProfile:
    if version <= active.version:
        raise ProfileSettingsError("new profile version must follow the active version")
    destination = _settings_text(values, "destination", maximum_length=500)
    max_commute = _settings_int(values, "max_commute_minutes", 1, 240)
    min_area = _settings_decimal(values, "min_area_sqm", Decimal("10"), Decimal("1000"))
    min_rooms = _settings_int(values, "min_rooms", 1, 20)
    max_price = _settings_money(values, "max_purchase_price_pln", 1, 1_000_000_000)
    core_price = _settings_money(values, "core_purchase_price_pln", 1, 1_000_000_000)
    installment = _settings_money(values, "max_monthly_installment_pln", 1, 1_000_000)
    cash_budget = _settings_money(values, "cash_budget_pln", 1, 1_000_000_000)
    building_dwellings = _settings_int(values, "max_building_dwellings", 1, 10_000)
    ideal_low = _settings_decimal(
        values, "ideal_area_low_sqm", Decimal("10"), Decimal("1000")
    )
    ideal_high = _settings_decimal(
        values, "ideal_area_high_sqm", Decimal("10"), Decimal("1000")
    )
    admin_fee = _settings_money(values, "reference_admin_fee_pln", 1, 100_000)
    if core_price > max_price:
        raise ProfileSettingsError("core price cannot exceed maximum price")
    if not min_area <= ideal_low <= ideal_high:
        raise ProfileSettingsError(
            "ideal area must start at or above minimum and end at or above its start"
        )
    heating = _single_form_value(values, "preferred_heating_type")
    if heating not in _HEATING_LABELS:
        raise ProfileSettingsError("preferred heating type is invalid")
    localities_raw = _single_form_value(values, "excluded_localities")
    localities = frozenset(
        item.strip().casefold()
        for item in re.split(r"[,\n]", localities_raw)
        if item.strip()
    )
    if len(localities) > 50 or any(len(item) > 100 for item in localities):
        raise ProfileSettingsError("excluded localities are invalid")
    weights = tuple(
        (
            name,
            _settings_decimal(values, f"weight_{name}", Decimal("0"), Decimal("100")),
        )
        for name, _ in _SCORE_WEIGHT_FIELDS
    )
    if sum((weight for _, weight in weights), Decimal("0")) != Decimal("100"):
        raise ProfileSettingsError("score weights must add up to 100")
    return BuyerProfile(
        version=version,
        effective_from=effective_from,
        destination=destination,
        max_commute_minutes=max_commute,
        min_area_sqm=min_area,
        min_rooms=min_rooms,
        max_purchase_price_minor=max_price,
        core_purchase_price_minor=core_price,
        max_monthly_installment_minor=installment,
        reference_admin_fee_including_heating_minor=admin_fee,
        preferred_heating_type=heating,
        cash_budget_minor=cash_budget,
        max_building_dwellings=building_dwellings,
        excluded_localities=localities,
        ideal_area_low_sqm=ideal_low,
        ideal_area_high_sqm=ideal_high,
        score_weights=weights,
    )


def _single_form_value(values: Mapping[str, list[str]], name: str) -> str:
    items = values.get(name)
    if items is None or len(items) != 1:
        raise ProfileSettingsError(f"field {name} is required exactly once")
    return items[0].strip()


def _settings_text(
    values: Mapping[str, list[str]], name: str, *, maximum_length: int
) -> str:
    value = _single_form_value(values, name)
    if not value or len(value) > maximum_length:
        raise ProfileSettingsError(f"field {name} is invalid")
    return value


def _settings_int(
    values: Mapping[str, list[str]], name: str, minimum: int, maximum: int
) -> int:
    raw = _single_form_value(values, name)
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise ProfileSettingsError(f"field {name} must be an integer")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ProfileSettingsError(f"field {name} is outside the allowed range")
    return value


def _settings_decimal(
    values: Mapping[str, list[str]],
    name: str,
    minimum: Decimal,
    maximum: Decimal,
) -> Decimal:
    raw = _single_form_value(values, name).replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ProfileSettingsError(f"field {name} must be a number") from error
    if not value.is_finite() or not minimum <= value <= maximum:
        raise ProfileSettingsError(f"field {name} is outside the allowed range")
    return value


def _settings_money(
    values: Mapping[str, list[str]], name: str, minimum: int, maximum: int
) -> int:
    amount = _settings_decimal(values, name, Decimal(minimum), Decimal(maximum))
    minor = amount * 100
    if minor != minor.to_integral_value():
        raise ProfileSettingsError(f"field {name} supports at most two decimals")
    return int(minor)


def _render_profile_settings(
    profile: BuyerProfile, *, csrf_token: str, saved: bool
) -> str:
    weights = profile.weights()
    heating_options = "".join(
        f'<option value="{escape(value, quote=True)}"'
        f"{' selected' if value == profile.preferred_heating_type else ''}>"
        f"{escape(label)}</option>"
        for value, label in _HEATING_LABELS.items()
    )
    weight_fields = "".join(
        _settings_input(
            f"weight_{name}", label, str(weights.get(name, Decimal("0"))), step="0.1"
        )
        for name, label in _SCORE_WEIGHT_FIELDS
    )
    notice = (
        "<p class=notice>Nowa wersja kryteriów została zapisana i aktywowana.</p>"
        if saved
        else ""
    )
    fixed_rules = (
        "<ul><li>Zakup nieruchomości, nie najem</li>"
        "<li>Brak poważnego ryzyka prawnego</li>"
        "<li>Wydanie lokalu bez lokatorów i odrębna własność</li>"
        "<li>Użyteczny układ, wymagania piętra/windy i możliwość parkowania</li></ul>"
    )
    hard_fields = "".join(
        (
            _settings_input(
                "destination", "Cel dojazdu", profile.destination, input_type="text"
            ),
            _settings_input(
                "max_commute_minutes",
                "Maksymalny dojazd (min)",
                profile.max_commute_minutes,
            ),
            _settings_input(
                "min_area_sqm",
                "Minimalny metraż (m²)",
                profile.min_area_sqm,
                step="0.1",
            ),
            _settings_input("min_rooms", "Minimalna liczba pokoi", profile.min_rooms),
            _settings_input(
                "max_purchase_price_pln",
                "Maksymalna cena (PLN)",
                _pln_form(profile.max_purchase_price_minor),
                step="0.01",
            ),
            _settings_input(
                "core_purchase_price_pln",
                "Cena bazowa (PLN)",
                _pln_form(profile.core_purchase_price_minor),
                step="0.01",
            ),
            _settings_input(
                "max_monthly_installment_pln",
                "Maksymalna rata (PLN)",
                _pln_form(profile.max_monthly_installment_minor),
                step="0.01",
            ),
            _settings_input(
                "cash_budget_pln",
                "Budżet gotówkowy (PLN)",
                _pln_form(profile.cash_budget_minor),
                step="0.01",
            ),
            _settings_input(
                "max_building_dwellings",
                "Maksymalna liczba mieszkań w budynku",
                profile.max_building_dwellings,
            ),
            _settings_input(
                "excluded_localities",
                "Wykluczone miejscowości (po przecinku)",
                ", ".join(sorted(profile.excluded_localities)),
                input_type="text",
            ),
        )
    )
    preference_fields = "".join(
        (
            _settings_input(
                "ideal_area_low_sqm",
                "Idealny metraż od (m²)",
                profile.ideal_area_low_sqm,
                step="0.1",
            ),
            _settings_input(
                "ideal_area_high_sqm",
                "Idealny metraż do (m²)",
                profile.ideal_area_high_sqm,
                step="0.1",
            ),
            _settings_input(
                "reference_admin_fee_pln",
                "Czynsz referencyjny z ogrzewaniem (PLN)",
                _pln_form(profile.reference_admin_fee_including_heating_minor),
                step="0.01",
            ),
        )
    )
    csrf_field = (
        '<input type=hidden name="csrf_token" value="'
        f'{escape(csrf_token, quote=True)}">'
    )
    return (
        "<!doctype html><html lang=pl><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width'>"
        "<title>Ustawienia kryteriów Homez</title><style>body{font-family:system-ui,"
        "sans-serif;max-width:70rem;margin:2rem auto;padding:0 1rem;color:#202124}"
        "fieldset{border:1px solid #ddd;border-radius:.7rem;margin:1rem 0;padding:1rem}"
        "label{display:grid;grid-template-columns:minmax(14rem,1fr) minmax(10rem,1fr);"
        "gap:.8rem;align-items:center;margin:.65rem 0}input,select,button{font:inherit;"
        "padding:.5rem;box-sizing:border-box}input,select{width:100%}.notice{background:"
        "#e6f4ea;padding:.8rem;border-radius:.5rem}.help{color:#555}button{margin-top:"
        ".8rem}@media(max-width:40rem){label{grid-template-columns:1fr;gap:.2rem}}"
        "</style><body><main><p><a href='/feedback/offers'>← Oferty</a> · "
        "<a href='/feedback/queue'>Kolejka</a></p><h1>Ustawienia kryteriów</h1>"
        f"<p>Aktywna wersja: {profile.version} · obowiązuje od "
        f"{escape(profile.effective_from.isoformat())}</p>{notice}"
        "<form method=post action='/feedback/settings'>"
        f"{csrf_field}"
        "<fieldset><legend>Wyszukiwanie i twarde limity</legend>"
        f"{hard_fields}"
        "</fieldset><fieldset><legend>Preferencje</legend>"
        f"{preference_fields}"
        '<label>Preferowane ogrzewanie<select name="preferred_heating_type">'
        f"{heating_options}</select></label></fieldset>"
        "<fieldset><legend>Wagi oceny (łącznie 100)</legend>"
        f"{weight_fields}</fieldset><fieldset><legend>Stałe reguły bezpieczeństwa"
        "</legend><p class=help>Te reguły są widoczne, ale nie można ich zmienić "
        f"z panelu.</p>{fixed_rules}</fieldset>"
        "<button type=submit>Zapisz jako nową aktywną wersję</button></form>"
        "</main></body></html>"
    )


def _settings_input(
    name: str,
    label: str,
    value: object,
    *,
    input_type: str = "number",
    step: str = "1",
) -> str:
    return (
        f'<label>{escape(label)}<input type="{input_type}" name="'
        f'{escape(name, quote=True)}" value="{escape(str(value), quote=True)}"'
        f"{' step=' + repr(step) if input_type == 'number' else ''} required></label>"
    )


def _pln_form(value_minor: int) -> str:
    return f"{Decimal(value_minor) / Decimal(100):.2f}"


_FEEDBACK_LABELS = {
    "like": "Podoba mi się",
    "dislike": "Nie podoba mi się",
    "save": "Zapisane na później",
}
_REASON_LABELS = {
    "too_expensive": "Za wysoka cena",
    "too_high_admin_fee": "Za wysoki czynsz",
    "unsuitable_heating": "Nieodpowiednie ogrzewanie",
    "wrong_location": "Nieodpowiednia lokalizacja",
    "too_small": "Za mały metraż",
    "too_few_rooms": "Za mało pokoi",
    "poor_condition": "Zły stan / za duży remont",
    "bad_layout": "Nieodpowiedni układ",
    "commute": "Zbyt długi dojazd",
    "floor_or_no_elevator": "Piętro lub brak windy",
    "no_parking": "Brak możliwości parkowania",
    "legal_risk": "Ryzyko prawne",
    "other": "Inny powód",
}


def _render_offer_browser(
    sessions: sessionmaker[Session],
    *,
    feedback_filter: str,
    page: int,
    csrf_token: str,
    report_status: str | None,
    page_size: int = 50,
) -> str:
    with sessions() as session:
        listings = session.execute(
            select(ListingRecord, SourceRecord)
            .join(SourceRecord, SourceRecord.id == ListingRecord.source_id)
            .order_by(ListingRecord.title, ListingRecord.id)
        ).all()
        snapshots = session.scalars(
            select(ListingSnapshotRecord).order_by(
                ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id
            )
        ).all()
        events = session.scalars(
            select(FeedbackEventRecord).order_by(
                FeedbackEventRecord.recorded_at.desc(), FeedbackEventRecord.id
            )
        ).all()

    latest_snapshots: dict[str, ListingSnapshotRecord] = {}
    for snapshot in snapshots:
        latest_snapshots.setdefault(str(snapshot.listing_id), snapshot)
    latest_feedback: dict[str, FeedbackEventRecord] = {}
    for event in events:
        latest_feedback.setdefault(event.listing_id, event)

    rows = [
        (
            listing,
            source,
            latest_snapshots.get(str(listing.id)),
            latest_feedback.get(str(listing.id)),
        )
        for listing, source in listings
    ]
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(
        key=lambda row: row[2].observed_at if row[2] is not None else oldest,
        reverse=True,
    )
    with_feedback = sum(event is not None for _, _, _, event in rows)
    counts = {
        "all": len(rows),
        "with_feedback": with_feedback,
        "without_feedback": len(rows) - with_feedback,
    }
    if feedback_filter == "with_feedback":
        rows = [row for row in rows if row[3] is not None]
    elif feedback_filter == "without_feedback":
        rows = [row for row in rows if row[3] is None]

    total = len(rows)
    start = (page - 1) * page_size
    visible = rows[start : start + page_size]
    tabs = "".join(
        _offer_filter_link(key, label, counts[key], feedback_filter)
        for key, label in (
            ("all", "Wszystkie"),
            ("without_feedback", "Bez feedbacku"),
            ("with_feedback", "Z feedbackiem"),
        )
    )
    cards = (
        "".join(
            _offer_card(listing, source, snapshot, event, csrf_token=csrf_token)
            for listing, source, snapshot, event in visible
        )
        or "<p>Brak ofert dla wybranego filtra.</p>"
    )
    navigation = _offer_navigation(
        feedback_filter=feedback_filter,
        page=page,
        has_previous=page > 1,
        has_next=start + page_size < total,
    )
    notice = (
        "<p class=notice>Raport został przygotowany i dodany do kolejki wysyłki.</p>"
        if report_status == "queued"
        else ""
    )
    report_form = (
        "<form method=post action='/feedback/offers/report'>"
        f"<input type=hidden name=csrf_token value='{escape(csrf_token, quote=True)}'>"
        "<button type=submit>Wygeneruj i wyślij raport teraz</button></form>"
    )
    queue_link = (
        "<p><a href='/feedback/queue'>Status kolejki</a> · "
        "<a href='/feedback/scraper-errors'>Błędy scraperów</a> · "
        "<a href='/feedback/settings'>Ustawienia kryteriów</a></p>"
    )
    return (
        "<!doctype html><html lang=pl><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width'><title>Oferty Homez</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:70rem;margin:2rem "
        "auto;padding:0 1rem;color:#202124}nav{display:flex;gap:.6rem;flex-wrap:wrap;"
        "margin:1rem 0}nav a{padding:.55rem .8rem;border:1px solid #bbb;"
        "border-radius:.5rem;text-decoration:none;color:inherit}.active{background:#202124;"
        "color:white}article{border:1px solid #ddd;border-radius:.7rem;padding:1rem;"
        "margin:1rem 0}h2{font-size:1.15rem;margin:.2rem 0}.facts{color:#555}"
        ".feedback{background:#f4f5f6;padding:.7rem;border-radius:.4rem}"
        "details{margin-top:.8rem}fieldset{border:0;padding:0}label{display:block;"
        "margin:.4rem 0}select,textarea,button{font:inherit;padding:.45rem;"
        "max-width:100%}textarea{width:100%;box-sizing:border-box}"
        ".notice{background:#e6f4ea;padding:.7rem;border-radius:.4rem}</style>"
        f"<body><main><h1>Oferty Homez</h1>{notice}{report_form}{queue_link}"
        f"<nav aria-label='Filtr ofert'>{tabs}</nav>"
        f"<p>Wyników: {total}</p>{cards}{navigation}</main></body></html>"
    )


_QUEUE_STATES = (
    "pending",
    "running",
    "retry_wait",
    "succeeded",
    "dead_letter",
    "manual_review",
)
_ACTIVE_QUEUE_STATES = frozenset(("pending", "running", "retry_wait"))
_QUEUE_STATE_LABELS = {
    "pending": "Oczekujące",
    "running": "W toku",
    "retry_wait": "Ponowienie",
    "succeeded": "Zakończone",
    "dead_letter": "Błąd końcowy",
    "manual_review": "Wymaga decyzji",
}
_QUEUE_KIND_LABELS = {
    "poll": "Pobieranie poczty",
    "normalize": "Scraping i normalizacja",
    "enrich": "Wzbogacanie",
    "match": "Ocena ofert",
    "report": "Raporty",
}


def _render_queue_status(sessions: sessionmaker[Session], *, now: datetime) -> str:
    with sessions() as session:
        aggregate_rows = session.execute(
            select(
                WorkflowJobRecord.kind,
                WorkflowJobRecord.state,
                func.count(WorkflowJobRecord.id),
                func.min(WorkflowJobRecord.created_at),
                func.max(WorkflowJobRecord.updated_at),
            ).group_by(WorkflowJobRecord.kind, WorkflowJobRecord.state)
        ).all()
        normalization_rows = session.execute(
            select(WorkflowJobRecord.payload_json, WorkflowJobRecord.state).where(
                WorkflowJobRecord.kind == "normalize",
                WorkflowJobRecord.state.in_(_ACTIVE_QUEUE_STATES),
            )
        ).all()
        error_rows = session.execute(
            select(
                WorkflowJobRecord.last_error_code,
                WorkflowJobRecord.state,
                func.count(WorkflowJobRecord.id),
            )
            .where(WorkflowJobRecord.last_error_code.is_not(None))
            .group_by(WorkflowJobRecord.last_error_code, WorkflowJobRecord.state)
            .order_by(WorkflowJobRecord.state, WorkflowJobRecord.last_error_code)
        ).all()

        listing_ids = {
            listing_id
            for payload_json, _ in normalization_rows
            if (listing_id := _queue_listing_id(payload_json)) is not None
        }
        source_by_listing = {
            listing_id: display_name
            for listing_id, display_name in session.execute(
                select(ListingRecord.id, SourceRecord.display_name)
                .join(SourceRecord, SourceRecord.id == ListingRecord.source_id)
                .where(ListingRecord.id.in_(listing_ids))
            )
        }

    counts: dict[str, dict[str, int]] = {}
    totals = {state: 0 for state in _QUEUE_STATES}
    oldest_active: datetime | None = None
    latest_activity: datetime | None = None
    for kind, state, count, oldest, latest in aggregate_rows:
        counts.setdefault(kind, {})[state] = count
        if state in totals:
            totals[state] += count
        if state in _ACTIVE_QUEUE_STATES and (
            oldest_active is None or oldest < oldest_active
        ):
            oldest_active = oldest
        if latest_activity is None or latest > latest_activity:
            latest_activity = latest

    source_counts: dict[str, dict[str, int]] = {}
    for payload_json, state in normalization_rows:
        listing_id = _queue_listing_id(payload_json)
        source = source_by_listing.get(listing_id, "Nieznane")
        by_state = source_counts.setdefault(source, {})
        by_state[state] = by_state.get(state, 0) + 1

    cards = "".join(
        "<div class=card><span>"
        f"{escape(_QUEUE_STATE_LABELS[state])}</span><strong>{totals[state]}</strong>"
        "</div>"
        for state in (
            "pending",
            "running",
            "retry_wait",
            "dead_letter",
            "manual_review",
        )
    )
    kind_rows = (
        "".join(
            "<tr><th scope=row>"
            f"{escape(_QUEUE_KIND_LABELS.get(kind, kind))}</th>"
            + "".join(f"<td>{by_state.get(state, 0)}</td>" for state in _QUEUE_STATES)
            + f"<td>{sum(by_state.values())}</td></tr>"
            for kind, by_state in sorted(counts.items())
        )
        or "<tr><td colspan=8>Brak zadań.</td></tr>"
    )
    source_rows = (
        "".join(
            "<tr><th scope=row>"
            f"{escape(source)}</th>"
            f"<td>{by_state.get('pending', 0)}</td>"
            f"<td>{by_state.get('running', 0)}</td>"
            f"<td>{by_state.get('retry_wait', 0)}</td>"
            f"<td>{sum(by_state.values())}</td></tr>"
            for source, by_state in sorted(source_counts.items())
        )
        or "<tr><td colspan=5>Brak aktywnych zadań normalizacji.</td></tr>"
    )
    errors = (
        "".join(
            "<tr><td>"
            f"{escape(error_code or 'nieznany')}</td>"
            f"<td>{escape(_QUEUE_STATE_LABELS.get(state, state))}</td>"
            f"<td>{count}</td></tr>"
            for error_code, state, count in error_rows
        )
        or "<tr><td colspan=3>Brak błędów.</td></tr>"
    )
    headings = "".join(
        f"<th scope=col>{escape(_QUEUE_STATE_LABELS[state])}</th>"
        for state in _QUEUE_STATES
    )
    return (
        "<!doctype html><html lang=pl><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width'>"
        "<meta http-equiv=refresh content=15><title>Kolejka Homez</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:75rem;margin:2rem "
        "auto;padding:0 1rem;color:#202124}.cards{display:flex;gap:.7rem;"
        "flex-wrap:wrap}.card{min-width:9rem;border:1px solid #ddd;border-radius:.7rem;"
        "padding:.8rem}.card span,.card strong{display:block}.card strong{font-size:"
        "1.6rem;margin-top:.25rem}table{border-collapse:collapse;width:100%;"
        "margin:1rem 0 2rem}th,td{text-align:left;border-bottom:1px solid #ddd;"
        "padding:.55rem}th{white-space:nowrap}.meta{color:#555}a{color:inherit}</style>"
        "<body><main><p><a href='/feedback/offers'>← Oferty</a> · "
        "<a href='/feedback/settings'>Ustawienia kryteriów</a> · "
        "<a href='/feedback/scraper-errors'>Błędy scraperów</a></p>"
        "<h1>Kolejka Homez</h1>"
        f"<p class=meta>Stan na {_queue_time(now)} · "
        "automatyczne odświeżanie co 15 s</p>"
        f"<div class=cards>{cards}</div>"
        "<h2>Wszystkie etapy</h2><table><thead><tr><th scope=col>Etap</th>"
        f"{headings}<th scope=col>Razem</th></tr></thead><tbody>{kind_rows}</tbody>"
        "</table><h2>Aktywna normalizacja według portalu</h2><table><thead><tr>"
        "<th scope=col>Portal</th><th scope=col>Oczekujące</th>"
        "<th scope=col>W toku</th><th scope=col>Ponowienie</th>"
        f"<th scope=col>Razem</th></tr></thead><tbody>{source_rows}</tbody></table>"
        "<h2>Kody błędów</h2><table><thead><tr><th scope=col>Kod</th>"
        "<th scope=col>Stan</th><th scope=col>Liczba</th></tr></thead>"
        f"<tbody>{errors}</tbody></table><p class=meta>Najstarsze aktywne zadanie: "
        f"{_queue_time(oldest_active)}<br>Ostatnia zmiana: "
        f"{_queue_time(latest_activity)}</p></main></body></html>"
    )


def _queue_listing_id(payload_json: str) -> UUID | None:
    try:
        value = json.loads(payload_json).get("listing_id")
        return UUID(value) if isinstance(value, str) else None
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _queue_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return escape(value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))


def _offer_filter_link(key: str, label: str, count: int, selected: str) -> str:
    css_class = " class=active" if key == selected else ""
    href = "/feedback/offers?" + urlencode({"feedback": key})
    return f"<a{css_class} href='{href}'>{label} ({count})</a>"


def _offer_card(
    listing: ListingRecord,
    source: SourceRecord,
    snapshot: ListingSnapshotRecord | None,
    event: FeedbackEventRecord | None,
    *,
    csrf_token: str,
) -> str:
    facts = "Brak szczegółów"
    if snapshot is not None:
        price = f"{snapshot.price_minor / 100:,.0f}".replace(",", " ")
        area = str(snapshot.area_sqm) if snapshot.area_sqm is not None else "?"
        rooms = str(snapshot.rooms) if snapshot.rooms is not None else "?"
        location = escape(snapshot.location or "nieznana")
        facts = (
            f"{price} {escape(snapshot.currency)} · {area} m² · "
            f"{rooms} pok. · {location}"
        )
    feedback = "<p class=feedback>Brak feedbacku</p>"
    if event is not None:
        value = escape(_FEEDBACK_LABELS.get(event.value, event.value))
        reason = (
            f" · {escape(_REASON_LABELS.get(event.reason_code, event.reason_code))}"
            if event.reason_code
            else ""
        )
        comment = f"<br>{escape(event.comment)}" if event.comment else ""
        feedback = f"<p class=feedback><strong>{value}</strong>{reason}{comment}</p>"
    form = _offer_feedback_form(
        listing_id=str(listing.id), csrf_token=csrf_token, event=event
    )
    return (
        "<article>"
        f"<small>{escape(source.display_name)}</small>"
        f"<h2>{escape(listing.title)}</h2><p class=facts>{facts}</p>{feedback}"
        f"<a href='{escape(listing.canonical_url, quote=True)}' "
        "rel='noreferrer noopener' target=_blank>Otwórz ogłoszenie</a>"
        f"{form}</article>"
    )


def _offer_feedback_form(
    *, listing_id: str, csrf_token: str, event: FeedbackEventRecord | None
) -> str:
    summary = "Zmień feedback" if event is not None else "Dodaj feedback"
    current = event.value if event is not None else None
    reason = event.reason_code if event is not None else None
    comment = escape(event.comment) if event is not None and event.comment else ""
    options = "".join(
        f"<option value='{code}'{' selected' if code == reason else ''}>"
        f"{escape(label)}</option>"
        for code, label in _REASON_LABELS.items()
    )
    return (
        f"<details><summary>{summary}</summary>"
        f"<form method=post action='/feedback/offers/{listing_id}'>"
        f"<input type=hidden name=csrf_token value='{escape(csrf_token, quote=True)}'>"
        "<fieldset><legend>Ocena</legend>"
        f"<label><input type=radio name=value value=like required"
        f"{' checked' if current == 'like' else ''}> Podoba mi się</label>"
        f"<label><input type=radio name=value value=dislike"
        f"{' checked' if current == 'dislike' else ''}> Nie podoba mi się</label>"
        f"<label><input type=radio name=value value=save"
        f"{' checked' if current == 'save' else ''}> Zapisz na później</label>"
        "</fieldset><label>Powód odrzucenia (wymagany dla „Nie podoba mi się”):"
        f"<select name=reason_code><option value=''>Wybierz powód</option>"
        f"{options}</select></label><label>Komentarz (opcjonalny):"
        f"<textarea name=comment maxlength=500 rows=3>{comment}</textarea></label>"
        "<button type=submit>Zapisz feedback</button></form></details>"
    )


def _offer_navigation(
    *, feedback_filter: str, page: int, has_previous: bool, has_next: bool
) -> str:
    links: list[str] = []
    if has_previous:
        href = "/feedback/offers?" + urlencode(
            {"feedback": feedback_filter, "page": page - 1}
        )
        links.append(f"<a href='{href}'>← Poprzednia</a>")
    if has_next:
        href = "/feedback/offers?" + urlencode(
            {"feedback": feedback_filter, "page": page + 1}
        )
        links.append(f"<a href='{href}'>Następna →</a>")
    return f"<nav aria-label='Stronicowanie'>{''.join(links)}</nav>"


def _queue_manual_report(
    settings: Settings,
    sessions: sessionmaker[Session],
    *,
    now: datetime,
) -> None:
    if settings.report_recipient_file is None:
        raise HTTPException(status_code=503, detail="report recipient unavailable")
    period = "M" + _base36(int(now.timestamp() // 60)).rjust(7, "0")[-7:]
    report_id = WorkflowService(sessions).prepare_report(
        period=period,
        cutoff_at=now,
        routing_goal_version=1,
        now=now,
    )
    with sessions() as session:
        report = session.get(ReportDraftRecord, report_id)
    if report is None or report.status != "prepared":
        raise HTTPException(status_code=503, detail="report preparation failed")
    try:
        recipient = read_secret_text(settings.report_recipient_file)
    except TokenError as error:
        raise HTTPException(
            status_code=503, detail="report recipient unavailable"
        ) from error
    DeliveryOutbox(sessions).enqueue(
        period=period,
        report_id=str(report.id),
        recipient=recipient,
        render_version=report.render_version,
        now=now,
    )


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    while value:
        value, remainder = divmod(value, len(alphabet))
        encoded = alphabet[remainder] + encoded
    return encoded or "0"


app = create_app()
