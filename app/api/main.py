from contextlib import asynccontextmanager
from datetime import date, time
from decimal import Decimal
from hmac import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, validate_runtime
from app.database.models import Booking, Service
from app.database.session import engine, get_session


@asynccontextmanager
async def lifespan(application: FastAPI):
    validate_runtime(get_settings(), api=True)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="AutoBook Bot API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    import logging

    logging.getLogger(__name__).error("Database request failed: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    price_from: Decimal
    duration_minutes: int
    is_active: bool


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    telegram_user_id: int
    customer_name: str
    phone: str
    car: str
    service_id: int
    booking_date: date
    start_time: time
    end_time: time
    comment: str | None
    status: str


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    configured = get_settings().api_key
    if not configured or not x_api_key or not compare_digest(configured, x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/services", response_model=list[ServiceOut])
async def services(session: Annotated[AsyncSession, Depends(get_session)]) -> list[Service]:
    return list(
        (await session.scalars(select(Service).where(Service.is_active).order_by(Service.id))).all()
    )


@app.get("/api/bookings", response_model=list[BookingOut], dependencies=[Depends(require_api_key)])
async def bookings(session: Annotated[AsyncSession, Depends(get_session)]) -> list[Booking]:
    return list(
        (
            await session.scalars(
                select(Booking)
                .order_by(Booking.booking_date.desc(), Booking.start_time.desc())
                .limit(500)
            )
        ).all()
    )
