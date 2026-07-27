"""FastAPI application entry point."""

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response returned when the API process is healthy."""

    status: str


app = FastAPI(title="Carrier Pool API", version="0.1.0")


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Return process health without depending on future infrastructure."""
    return HealthResponse(status="ok")
