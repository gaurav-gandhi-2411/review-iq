"""Review IQ — FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(
    title="Review IQ",
    description="Unstructured customer reviews → queryable structured insights.",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
