"""SQLAlchemy models. Money is stored as strings via the Money type and handled as Decimal."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, create_engine, types
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool


class Money(types.TypeDecorator):
    impl = String
    cache_ok = True
    def process_bind_param(self, v, d): return None if v is None else str(Decimal(v))
    def process_result_value(self, v, d): return None if v is None else Decimal(v)


def now(): return datetime.now(timezone.utc)


class Base(DeclarativeBase): pass


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(2))
    max_discount_pct: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    keywords: Mapped[str] = mapped_column(Text)  # comma separated, used for deterministic matching
    unit: Mapped[str] = mapped_column(String(20), default="each")


class Warehouse(Base):
    __tablename__ = "warehouses"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    region: Mapped[str] = mapped_column(String(2))


class Inventory(Base):
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    on_hand: Mapped[int] = mapped_column(Integer)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    product: Mapped[Product] = relationship()


class DeliveryRule(Base):
    __tablename__ = "delivery_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    region: Mapped[str] = mapped_column(String(2))
    lead_days: Mapped[int] = mapped_column(Integer)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    name: Mapped[str] = mapped_column(String(120))
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None => UNKNOWN


class Compatibility(Base):
    __tablename__ = "compatibility"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    substitute_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")


class PriceBook(Base):
    __tablename__ = "price_books"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PriceBookEntry(Base):
    __tablename__ = "price_book_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    price_book_id: Mapped[int] = mapped_column(ForeignKey("price_books.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    price: Mapped[Decimal] = mapped_column(Money)


class WorkflowRequest(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[str] = mapped_column(String(36), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(40), default="RECEIVED")
    failed_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fulfillment: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decisions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class Quote(Base):
    __tablename__ = "quotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_number: Mapped[str] = mapped_column(String(20), unique=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    lines: Mapped[list] = mapped_column(JSON)
    subtotal: Mapped[Decimal] = mapped_column(Money)
    discount_pct: Mapped[Decimal] = mapped_column(Money)
    discount_amount: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT|APPROVED|REJECTED|FINAL
    created_at: Mapped[datetime] = mapped_column(default=now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"), unique=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    required_role: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"))
    ts: Mapped[datetime] = mapped_column(default=now)
    event_type: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str] = mapped_column(String(60))
    from_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


def make_session_factory(url: str = "sqlite:///./buildmate.db"):
    kw = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in url or url == "sqlite://":
        kw["poolclass"] = StaticPool
    engine = create_engine(url, **kw)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
