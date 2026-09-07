import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from html import escape
from typing import Literal
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from homefinder.catalog.orm import (
    FeedbackEventRecord,
    ListingRecord,
    ListingSnapshotRecord,
    SourceRecord,
)
from homefinder.config import Environment, Settings
from homefinder.digest.feedback import (
    FeedbackError,
    SqlAlchemyFeedbackService,
)
from homefinder.enrichment.environment import ManualCorrectionStore
from homefinder.operations.health import HealthRegistry, HealthState
from homefinder.operations.logging import setup_logging
from homefinder.sources.gmail import TokenError, read_secret_text


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

    @application.get("/offers", response_class=HTMLResponse)
    def offers(
        authorization: str | None = Header(default=None),
        feedback: Literal["all", "with_feedback", "without_feedback"] = "all",
        page: int = 1,
    ) -> HTMLResponse:
        _require_offer_browser_admin(application.state.settings, authorization)
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be positive")
        return HTMLResponse(
            _render_offer_browser(
                application.state.sessions,
                feedback_filter=feedback,
                page=page,
            )
        )

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


_FEEDBACK_LABELS = {
    "like": "Podoba mi się",
    "dislike": "Nie podoba mi się",
    "save": "Zapisane na później",
}
_REASON_LABELS = {
    "too_expensive": "Za wysoka cena",
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
            _offer_card(listing, source, snapshot, event)
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
    return (
        "<!doctype html><html lang=pl><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width'><title>Oferty Homez</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:70rem;margin:2rem "
        "auto;padding:0 1rem;color:#202124}nav{display:flex;gap:.6rem;flex-wrap:wrap;"
        "margin:1rem 0}nav a{padding:.55rem .8rem;border:1px solid #bbb;"
        "border-radius:.5rem;text-decoration:none;color:inherit}.active{background:#202124;"
        "color:white}article{border:1px solid #ddd;border-radius:.7rem;padding:1rem;"
        "margin:1rem 0}h2{font-size:1.15rem;margin:.2rem 0}.facts{color:#555}"
        ".feedback{background:#f4f5f6;padding:.7rem;border-radius:.4rem}</style>"
        f"<body><main><h1>Oferty Homez</h1><nav aria-label='Filtr ofert'>{tabs}</nav>"
        f"<p>Wyników: {total}</p>{cards}{navigation}</main></body></html>"
    )


def _offer_filter_link(key: str, label: str, count: int, selected: str) -> str:
    css_class = " class=active" if key == selected else ""
    href = "/offers?" + urlencode({"feedback": key})
    return f"<a{css_class} href='{href}'>{label} ({count})</a>"


def _offer_card(
    listing: ListingRecord,
    source: SourceRecord,
    snapshot: ListingSnapshotRecord | None,
    event: FeedbackEventRecord | None,
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
    return (
        "<article>"
        f"<small>{escape(source.display_name)}</small>"
        f"<h2>{escape(listing.title)}</h2><p class=facts>{facts}</p>{feedback}"
        f"<a href='{escape(listing.canonical_url, quote=True)}' "
        "rel='noreferrer noopener' target=_blank>Otwórz ogłoszenie</a></article>"
    )


def _offer_navigation(
    *, feedback_filter: str, page: int, has_previous: bool, has_next: bool
) -> str:
    links: list[str] = []
    if has_previous:
        href = "/offers?" + urlencode({"feedback": feedback_filter, "page": page - 1})
        links.append(f"<a href='{href}'>← Poprzednia</a>")
    if has_next:
        href = "/offers?" + urlencode({"feedback": feedback_filter, "page": page + 1})
        links.append(f"<a href='{href}'>Następna →</a>")
    return f"<nav aria-label='Stronicowanie'>{''.join(links)}</nav>"


app = create_app()
