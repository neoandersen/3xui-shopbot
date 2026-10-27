from datetime import datetime
from typing import Optional
from decimal import Decimal
from sqlalchemy import BigInteger, String, Boolean, DateTime, Numeric, Float, func
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_spent: Mapped[float] = mapped_column(Float, default=0.0)
    total_months: Mapped[int] = mapped_column(default=0)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    agreed_to_terms: Mapped[bool] = mapped_column(Boolean, default=False)
    registration_date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    referred_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    referral_balance: Mapped[float] = mapped_column(Float, default=0.0)
    referral_balance_all: Mapped[float] = mapped_column(Float, default=0.0)
    referral_start_bonus_received: Mapped[bool] = mapped_column(Boolean, default=False)

    # Attribution & Onboarding fields (docs/SHOPBOT_AI_AND_ATTRIBUTION_SPEC.md)
    acquisition_source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    demand_segment: Mapped[str] = mapped_column(String(32), default="general")
    trial_granted: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_to_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    last_start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    preferred_currency: Mapped[str] = mapped_column(String(5), default="USD")
