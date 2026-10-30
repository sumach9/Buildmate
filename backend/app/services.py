"""Deterministic, independently testable business services. No LLM involvement."""
from __future__ import annotations
import hashlib, json
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .models import *

CENT = Decimal("0.01")
# Configurable policy (override via create_app(policy=...)).
DEFAULT_POLICY = {
    "rep_max_discount_pct": Decimal("5"),       # <= this: authorized sales rep
    "approver_role": "manager",                 # > this: manager approval
    "partial_requires_acceptance": True,
    "substitution_requires_acceptance": True,
    "weights": {"deadline": 0.4, "quantity": 0.3, "compat": 0.2, "cost": 0.1},
}


def money(v) -> Decimal: return Decimal(v).quantize(CENT, ROUND_HALF_UP)


# ---- lookups -------------------------------------------------------------------------------
def find_customer(db: Session, customer_id: int | None) -> Customer | None:
    return db.get(Customer, customer_id) if customer_id else None


def get_product(db: Session, sku: str) -> Product | None:
    return db.scalar(select(Product).where(Product.sku == sku))


def find_products(db: Session, text: str) -> list[Product]:
    """Longest-keyword match. Returns >1 product when the best match is tied (ambiguous), [] if unknown."""
    t, best, hits = text.lower(), 0, []
    for p in db.scalars(select(Product)):
        ks = [k.strip() for k in p.keywords.split(",") if k.strip() and k.strip() in t]
        if not ks: continue
        L = max(len(k) for k in ks)
        if L > best: best, hits = L, [p]
        elif L == best: hits.append(p)
    return hits


# ---- inventory -----------------------------------------------------------------------------
def available(db: Session, product_id: int) -> int:
    rows = db.execute(select(Inventory.on_hand, Inventory.reserved).where(Inventory.product_id == product_id)).all()
    return max(0, sum(o - r for o, r in rows))


def shortage(requested: int, avail: int) -> int: return max(0, requested - avail)


def compatible_substitutes(db: Session, product_id: int) -> list[Product]:
    """Only explicitly APPROVED mappings. Name similarity is never used."""
    ids = db.scalars(select(Compatibility.substitute_id).where(
        Compatibility.product_id == product_id, Compatibility.approved.is_(True))).all()
    return [db.get(Product, i) for i in ids]


def is_approved_substitute(db: Session, product_id: int, substitute_id: int) -> bool:
    return any(p.id == substitute_id for p in compatible_substitutes(db, product_id))


def delivery_feasibility(db: Session, deadline: date | None, today: date, region: str | None) -> tuple[str, str]:
    """Stock delivery feasibility from warehouse delivery rules. Missing evidence => UNKNOWN."""
    if deadline is None: return "UNKNOWN", "No delivery deadline supplied."
    lead = db.scalar(select(func.min(DeliveryRule.lead_days)).where(DeliveryRule.region == region)) if region else None
    if lead is None: return "UNKNOWN", f"No delivery rule on record for region {region}."
    eta = today + timedelta(days=lead)
    return ("FEASIBLE" if eta <= deadline else "INFEASIBLE"), f"Warehouse stock delivery ETA {eta} vs deadline {deadline}."


def supplier_eta(db: Session, product_id: int, today: date) -> date | None:
    lt = db.scalar(select(Supplier.lead_time_days).where(Supplier.product_id == product_id))
    return None if lt is None else today + timedelta(days=lt)


def evaluate_options(db, product: Product, requested: int, deadline, today, region, policy=DEFAULT_POLICY) -> dict:
    """Fulfillment option evaluation. Hard constraints (INFEASIBLE) override scoring; unknown stays UNKNOWN."""
    avail = available(db, product.id); short = shortage(requested, avail)
    stock_feas, stock_note = delivery_feasibility(db, deadline, today, region)
    w = policy["weights"]
    def score(dl, qty, compat, cost):
        d = {"FEASIBLE": 1.0, "UNKNOWN": 0.0, "INFEASIBLE": 0.0}[dl]
        return round(100 * (w["deadline"] * d + w["quantity"] * qty + w["compat"] * compat + w["cost"] * cost), 1)
    if short == 0:
        opt = dict(id="FULL", stock_quantity=requested, feasibility=stock_feas, requires_customer_acceptance=False,
                   score=score(stock_feas, 1, 1, 1), rationale=f"Full quantity in stock. {stock_note}")
        return dict(sku=product.sku, requested=requested, available=avail, shortage=0, options=[opt],
                    recommendation="FULL" if stock_feas != "INFEASIBLE" else "MANUAL_REVIEW",
                    rationale="Sufficient available inventory (on hand minus reserved).")
    opts = []
    if avail > 0:
        opts.append(dict(id="PARTIAL", stock_quantity=avail, feasibility=stock_feas,
                         requires_customer_acceptance=policy["partial_requires_acceptance"],
                         score=score(stock_feas, avail / requested, 1, 1),
                         rationale=f"Ship {avail} of {requested} now; {short} short. {stock_note}"))
    for sub in compatible_substitutes(db, product.id):
        sa = available(db, sub.id)
        if sa >= short:
            opts.append(dict(id="SUBSTITUTE", stock_quantity=avail, substitute_sku=sub.sku, substitute_quantity=short,
                             feasibility=stock_feas, requires_customer_acceptance=policy["substitution_requires_acceptance"],
                             score=score(stock_feas, 1, 0.8, 0.8),
                             rationale=f"Approved substitute {sub.name}: {short} units cover the shortage ({sa} available). {stock_note}"))
        else:
            opts.append(dict(id="SUBSTITUTE", stock_quantity=avail, substitute_sku=sub.sku, substitute_quantity=sa,
                             feasibility="INFEASIBLE", requires_customer_acceptance=True, score=None,
                             rationale=f"Approved substitute {sub.name} has only {sa} available; cannot cover {short}."))
    eta = supplier_eta(db, product.id, today)
    if eta is None:
        opts.append(dict(id="DELAYED", feasibility="UNKNOWN", requires_customer_acceptance=True, score=None,
                         rationale="No supplier lead time on record: delivery date UNKNOWN and will not be promised."))
    else:
        f = "FEASIBLE" if deadline and eta <= deadline else "INFEASIBLE"
        opts.append(dict(id="DELAYED", stock_quantity=avail, feasibility=f, requires_customer_acceptance=True,
                         score=score(f, 1, 1, 0.5), rationale=f"Supplier ETA {eta} vs deadline {deadline}."))
    opts.append(dict(id="MANUAL_REVIEW", feasibility="FEASIBLE", requires_customer_acceptance=False, score=None,
                     rationale="Escalate to a human for a manual fulfillment decision."))
    ok = [o for o in opts if o["feasibility"] == "FEASIBLE" and o["score"] is not None]
    best = max(ok, key=lambda o: o["score"]) if ok else None
    return dict(sku=product.sku, requested=requested, available=avail, shortage=short, options=opts,
                recommendation=best["id"] if best else "MANUAL_REVIEW",
                rationale=(f"Shortage of {short}. Recommended: {best['id']} (highest score among feasible options)."
                           if best else f"Shortage of {short} and no feasible automatic option."))


# ---- pricing & quotes ----------------------------------------------------------------------
class MissingPrice(Exception): pass


def get_price(db: Session, product_id: int) -> Decimal:
    p = db.scalar(select(PriceBookEntry.price).join(PriceBook).where(PriceBook.active.is_(True), PriceBookEntry.product_id == product_id))
    if p is None: raise MissingPrice(f"No approved price for product {product_id}")
    return p


def calc_quote(lines: list[tuple[Decimal, int]], discount_pct: Decimal) -> dict:
    """lines: (unit_price, quantity). Line totals rounded HALF_UP to cents, discount rounded HALF_UP."""
    totals = [money(Decimal(p) * q) for p, q in lines]
    subtotal = sum(totals, Decimal("0.00"))
    disc = money(subtotal * Decimal(discount_pct) / Decimal(100))
    return dict(line_totals=totals, subtotal=money(subtotal), discount_amount=disc, total=money(subtotal - disc))


def authorize_discount(pct: Decimal, policy=DEFAULT_POLICY) -> dict:
    if Decimal(pct) <= policy["rep_max_discount_pct"]:
        return dict(needs_approval=False, required_role="sales_rep", reason=f"Discount {pct}% within sales rep limit.")
    return dict(needs_approval=True, required_role=policy["approver_role"],
                reason=f"Discount {pct}% exceeds {policy['rep_max_discount_pct']}% limit; {policy['approver_role']} approval required.")


def quote_key(workflow_id: str, lines: list[dict], pct) -> str:
    payload = json.dumps([workflow_id, [[l["sku"], l["quantity"], str(l["unit_price"])] for l in lines], str(pct)])
    return hashlib.sha256(payload.encode()).hexdigest()


def request_key(customer_id, text: str) -> str:
    return hashlib.sha256(f"{customer_id}|{' '.join(text.lower().split())}".encode()).hexdigest()


def validate_finalization(db: Session, request: WorkflowRequest, quote: Quote, approval: Approval | None, policy=DEFAULT_POLICY) -> list[str]:
    """Final gate: decisions resolved, approvals present, inventory & prices revalidated."""
    errs = []
    if request.state != "APPROVED": errs.append(f"Workflow is {request.state}, not APPROVED.")
    if quote.status != "APPROVED": errs.append(f"Quote status is {quote.status}, not APPROVED.")
    if authorize_discount(quote.discount_pct, policy)["needs_approval"] and (approval is None or approval.status != "APPROVED"):
        errs.append("Required manager approval is missing.")
    for l in quote.lines:
        prod = get_product(db, l["sku"])
        if available(db, prod.id) < l["quantity"]:
            errs.append(f"Inventory changed: {l['sku']} needs {l['quantity']}, only {available(db, prod.id)} available.")
        try:
            if get_price(db, prod.id) != Decimal(str(l["unit_price"])):
                errs.append(f"Price changed for {l['sku']}.")
        except MissingPrice: errs.append(f"Price missing for {l['sku']}.")
    return errs
