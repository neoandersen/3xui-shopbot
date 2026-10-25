from decimal import Decimal
from sqlalchemy import String, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class TrafficSourcesStat(Base):
    __tablename__ = "traffic_sources_stat"

    source_tag: Mapped[str] = mapped_column(String(64), primary_key=True)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0)
    trials_count: Mapped[int] = mapped_column(Integer, default=0)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    total_income: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
