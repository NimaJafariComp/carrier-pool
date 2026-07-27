"""FastAPI application entry point."""

import os
from collections.abc import Generator
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from carrier_pool.db.models import Load, Stop
from carrier_pool.db.tenant import set_tenant_context


class HealthResponse(BaseModel):
    """Response returned when the API process is healthy."""

    status: str


app = FastAPI(title="Carrier Pool API", version="0.1.0")


class StopResponse(BaseModel):
    sequence: int
    is_pickup: bool
    is_dropoff: bool
    city: str
    state: str
    postal_code: str


class LoadResponse(BaseModel):
    id: str
    status: str
    equipment: str | None
    stops: list[StopResponse]


def database_session() -> Generator[Session]:
    """Open one app-role database session for a request."""
    engine = create_engine(os.environ["DATABASE_URL"])
    with Session(engine) as session:
        yield session


def tenant_context(x_tenant_id: str | None = Header(default=None)) -> UUID:
    """Accept only explicit UUID demo tenant context."""
    try:
        return UUID(x_tenant_id) if x_tenant_id is not None else (_ for _ in ()).throw(ValueError)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid tenant context.") from error


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Return process health without depending on future infrastructure."""
    return HealthResponse(status="ok")


@app.get("/api/v1/loads/{load_id}", response_model=LoadResponse, tags=["loads"])
def get_load(
    load_id: UUID,
    tenant_id: UUID = Depends(tenant_context),  # noqa: B008
    session: Session = Depends(database_session),  # noqa: B008
) -> LoadResponse:
    """Return current tenant-owned load; hide cross-tenant existence."""
    set_tenant_context(session, tenant_id)
    load = session.scalar(select(Load).where(Load.id == load_id, Load.tenant_id == tenant_id))
    if load is None:
        raise HTTPException(status_code=404, detail="Load not found.")
    stops = session.scalars(
        select(Stop)
        .where(Stop.load_id == load.id, Stop.tenant_id == tenant_id)
        .order_by(Stop.sequence)
    ).all()
    return LoadResponse(
        id=str(load.id),
        status=load.status.value,
        equipment=None if load.equipment is None else load.equipment.value,
        stops=[
            StopResponse(
                sequence=stop.sequence,
                is_pickup=stop.is_pickup,
                is_dropoff=stop.is_dropoff,
                city=stop.city,
                state=stop.state,
                postal_code=stop.postal_code,
            )
            for stop in stops
        ],
    )
