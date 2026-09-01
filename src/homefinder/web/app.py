from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from homefinder.config import Settings
from homefinder.digest.feedback import FeedbackError
from homefinder.enrichment.environment import ManualCorrectionStore
from homefinder.operations.health import HealthRegistry, HealthState
from homefinder.operations.logging import setup_logging


class FeedbackPayload(BaseModel):
    token: str
    value: str


class CorrectionPayload(BaseModel):
    field: str
    value: str
    corrected_by: str
    reason: str


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Homefinder", docs_url=None, redoc_url=None)
    application.state.settings = settings or Settings()
    setup_logging(application.state.settings.log_level)
    application.state.feedback_service = None
    application.state.correction_store = ManualCorrectionStore()
    application.state.health_registry = HealthRegistry()
    application.state.health_registry.update("application", HealthState.OK)

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

    @application.post("/feedback/{report_id}/{listing_id}")
    def feedback(
        report_id: str,
        listing_id: str,
        payload: FeedbackPayload,
        request: Request,
        x_csrf_token: str | None = Header(default=None),
    ) -> dict[str, str]:
        service = application.state.feedback_service
        if service is None:
            raise HTTPException(status_code=503, detail="feedback unavailable")
        csrf = request.cookies.get("homefinder_csrf")
        try:
            service.record(
                method=request.method,
                token=payload.token,
                csrf_token=x_csrf_token or "",
                expected_csrf=csrf or "",
                value=payload.value,
                now=datetime.now(timezone.utc),
                report_id=report_id,
                listing_id=listing_id,
            )
        except FeedbackError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"status": "recorded"}

    @application.post("/corrections/{property_id}")
    def correction(property_id: str, payload: CorrectionPayload) -> dict[str, str]:
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


app = create_app()
