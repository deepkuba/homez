from fastapi import FastAPI

from homefinder.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Homefinder", docs_url=None, redoc_url=None)
    application.state.settings = settings or Settings()

    @application.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
