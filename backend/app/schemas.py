"""Pydantic contracts for agent output. All model output is validated against these (untrusted input)."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

Feas = Literal["FEASIBLE", "INFEASIBLE", "UNKNOWN"]


class ParsedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_text: str
    sku: str | None = None
    quantity: int = Field(gt=0, le=1_000_000)
    candidates: list[str] = []


class ParsedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["COMPLETE", "NEEDS_CLARIFICATION"]
    customer_id: int | None = None
    delivery_location: str | None = None
    delivery_date: date | None = None
    requested_discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    line_items: list[ParsedLine] = []
    clarification_questions: list[str] = []
    validation_errors: list[str] = []
    explanation: str = ""


class Option(BaseModel):
    id: Literal["FULL", "PARTIAL", "SUBSTITUTE", "DELAYED", "MANUAL_REVIEW"]
    stock_quantity: int = 0            # units of the requested SKU shipped from stock
    substitute_sku: str | None = None
    substitute_quantity: int = 0
    feasibility: Feas
    requires_customer_acceptance: bool
    score: float | None = None
    rationale: str


class LineFulfillment(BaseModel):
    sku: str
    requested: int
    available: int
    shortage: int
    options: list[Option] = []
    recommendation: str | None = None
    rationale: str = ""


class FulfillmentResult(BaseModel):
    lines: list[LineFulfillment]
    requested_subtotal: Decimal | None = None
    requires_customer_decision: bool
    requires_human_review: bool
    explanation: str = ""


class QuoteLine(BaseModel):
    sku: str
    name: str
    quantity: int = Field(gt=0)
    unit_price: Decimal
    line_total: Decimal


class QuoteDraft(BaseModel):
    lines: list[QuoteLine]
    subtotal: Decimal
    discount_pct: Decimal
    discount_amount: Decimal
    total: Decimal
    needs_approval: bool
    required_role: str
    explanation: str = ""
