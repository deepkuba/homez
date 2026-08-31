from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from homefinder.config import Settings
from homefinder.digest.feedback import FeedbackError


class FeedbackPayload(BaseModel):
    token: str
    value: str


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Homefinder", docs_url=None, redoc_url=None)
    application.state.settings = settings or Settings()
    application.state.feedback_service = None

    @application.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

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

    return application


app = create_app()
