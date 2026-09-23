from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    __abstract__ = True


class BookingStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(150))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Service(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price_from: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    __table_args__ = (
        CheckConstraint("price_from >= 0", name="service_price_nonnegative"),
        CheckConstraint("duration_minutes > 0", name="service_duration_positive"),
    )


class BusinessSetting(Base):
    __tablename__ = "business_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    customer_name: Mapped[str] = mapped_column(String(150))
    phone: Mapped[str] = mapped_column(String(20))
    car: Mapped[str] = mapped_column(String(120))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    booking_date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default=BookingStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    reminder_24h_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_2h_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship()
    service: Mapped[Service] = relationship()
    __table_args__ = (
        CheckConstraint("start_time < end_time", name="booking_time_order"),
        CheckConstraint(
            "status IN ('pending','confirmed','cancelled','completed')", name="booking_status_valid"
        ),
        Index("ix_bookings_user_date", "telegram_user_id", "booking_date"),
        Index("ix_bookings_date_status", "booking_date", "status"),
    )
