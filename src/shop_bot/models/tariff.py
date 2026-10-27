from typing import Optional
from sqlalchemy import Integer, String, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class Tariff(Base):
    __tablename__ = "plans"

    plan_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_name: Mapped[str] = mapped_column(String(255))
    plan_name: Mapped[str] = mapped_column(String(255))
    months: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    price_currency: Mapped[str] = mapped_column(String(5), default="USD")
