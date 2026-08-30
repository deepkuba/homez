from fastapi import FastAPI

app = FastAPI(title="Homefinder", docs_url=None, redoc_url=None)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}
