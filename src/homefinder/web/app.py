import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from html import escape
from urllib.parse import parse_qs

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
    application.state.feedback_service = feedback_service or SqlAlchemyFeedbackService(
        sessionmaker(engine, expire_on_commit=False)
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
            "<title>Homefinder feedback</title><h1>Feedback</h1>"
            f"<form method=post action='{form_action}'>"
            "<input id=feedback-token type=hidden name=token>"
            f"<input type=hidden name=csrf_token value='{escape(csrf, quote=True)}'>"
            "<button name=value value=like>Like</button>"
            "<button name=value value=dislike>Dislike</button>"
            "<button name=value value=save>Save</button></form>"
            f"<script nonce='{nonce}'>"
            "const t=location.hash.slice(1);"
            "document.getElementById('feedback-token').value=t;"
            "history.replaceState(null,'',location.pathname);"
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
            else:
                values = parse_qs(body.decode("utf-8"), strict_parsing=True)
                token = values["token"][0]
                value = values["value"][0]
                submitted_csrf = values["csrf_token"][0]
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
            "<!doctype html><title>Feedback recorded</title><p>Thank you.</p>"
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


app = create_app()
