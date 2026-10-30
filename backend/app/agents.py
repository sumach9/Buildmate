"""Three agent interfaces with deterministic mock implementations (no API key needed).
Agents return plain dicts; the coordinator validates them with Pydantic and business rules (untrusted)."""
from __future__ import annotations
import json, re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol
from . import services as S

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class ModelAdapter(Protocol):
    """Provider-neutral LLM adapter. Implement complete() for any provider; output is treated as untrusted."""
    def complete(self, system: str, prompt: str) -> str: ...


class SalesAgent(Protocol):
    def run(self, db, text: str, customer_id: int | None, today: date) -> dict: ...
class InventoryAgent(Protocol):
    def run(self, db, parsed: dict, today: date, policy: dict) -> dict: ...
class CommercialAgent(Protocol):
    def run(self, db, parsed: dict, fulfillment: dict, decisions: dict, policy: dict) -> dict: ...


class MockSalesAgent:
    def run(self, db, text, customer_id, today):
        errors, questions = [], []
        cust = S.find_customer(db, customer_id)
        if customer_id and not cust: errors.append(f"Customer ID {customer_id} not found.")
        if not customer_id: questions.append("Which customer is this request for?")
        t = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)
        pct = Decimal("0")
        if m := re.search(r"(\d+(?:\.\d+)?)\s*%", t): pct = Decimal(m.group(1)); t = t.replace(m.group(0), " ")
        lines = []
        for seg in re.split(r"[.,;\n]|\band\b", t):
            if not (q := re.search(r"\b(\d[\d,]*)\b", seg)): continue
            qty = int(q.group(1).replace(",", ""))
            hits = S.find_products(db, seg)
            if not hits:
                errors.append(f"Unknown product in '{seg.strip()}'."); questions.append(f"Which catalog product did you mean by '{seg.strip()}'?")
            elif len(hits) > 1:
                names = " or ".join(h.name for h in hits)
                questions.append(f"'{seg.strip()}' is ambiguous. Did you mean {names}?")
                lines.append(dict(raw_text=seg.strip(), sku=None, quantity=qty, candidates=[h.sku for h in hits]))
            else: lines.append(dict(raw_text=seg.strip(), sku=hits[0].sku, quantity=qty))
        if not lines and not errors: questions.append("Which products and quantities do you need?")
        date_ = None
        if m := re.search(r"(\d{4}-\d{2}-\d{2})", text): date_ = date.fromisoformat(m.group(1))
        elif m := re.search(r"\bby\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text.lower()):
            delta = (WEEKDAYS.index(m.group(1)) - today.weekday()) % 7 or 7
            date_ = today + timedelta(days=delta)
        else: questions.append("What delivery date do you need?")
        loc = (m.group(1) if (m := re.search(r"\bin ([A-Z][a-z]+)", text)) else (cust.city if cust else None))
        needs = bool(questions or errors or any(l["sku"] is None for l in lines))
        return dict(status="NEEDS_CLARIFICATION" if needs else "COMPLETE", customer_id=customer_id if cust else None,
                    delivery_location=loc, delivery_date=date_.isoformat() if date_ else None,
                    requested_discount_pct=str(pct), line_items=lines, clarification_questions=questions,
                    validation_errors=errors,
                    explanation=f"Parsed {len(lines)} line(s); requested discount {pct}% (a request only; policy decides).")


class LLMSalesAgent:
    """Example provider-neutral agent: asks a model for JSON. Output still goes through full validation."""
    def __init__(self, adapter: ModelAdapter): self.adapter = adapter
    def run(self, db, text, customer_id, today):
        out = self.adapter.complete("Return ONLY JSON matching ParsedRequest. Never invent IDs, SKUs, prices.",
                                    json.dumps({"text": text, "customer_id": customer_id}))
        return json.loads(out)


class MockInventoryAgent:
    def run(self, db, parsed, today, policy):
        deadline = date.fromisoformat(parsed["delivery_date"]) if parsed.get("delivery_date") else None
        region = "WA"  # TODO derive from customer/location; seed data covers WA only
        lines, req_total, price_ok = [], Decimal("0"), True
        for li in parsed["line_items"]:
            prod = S.get_product(db, li["sku"])
            lines.append(S.evaluate_options(db, prod, li["quantity"], deadline, today, region, policy))
            try: req_total += S.money(S.get_price(db, prod.id) * li["quantity"])
            except S.MissingPrice: price_ok = False
        short = [l for l in lines if l["shortage"] > 0]
        manual_only = [l for l in short if l["recommendation"] == "MANUAL_REVIEW"]
        deci = bool(short) and not manual_only
        return dict(lines=lines, requested_subtotal=str(S.money(req_total)) if price_ok else None,
                    requires_customer_decision=deci, requires_human_review=bool(manual_only),
                    explanation=(f"{len(short)} line(s) short; customer acceptance required for partial/substitute options."
                                 if short else "All lines fully available."))


class MockCommercialAgent:
    def run(self, db, parsed, fulfillment, decisions, policy):
        qty: dict[str, int] = {}
        for l in fulfillment["lines"]:
            if l["shortage"] == 0: qty[l["sku"]] = qty.get(l["sku"], 0) + l["requested"]; continue
            opt = next(o for o in l["options"] if o["id"] == decisions[l["sku"]])
            if opt["stock_quantity"]: qty[l["sku"]] = qty.get(l["sku"], 0) + opt["stock_quantity"]
            if opt.get("substitute_sku"): qty[opt["substitute_sku"]] = qty.get(opt["substitute_sku"], 0) + opt["substitute_quantity"]
        lines, pairs = [], []
        for sku, q in qty.items():
            p = S.get_product(db, sku); price = S.get_price(db, p.id)  # raises MissingPrice -> workflow FAILED
            lines.append(dict(sku=sku, name=p.name, quantity=q, unit_price=str(price))); pairs.append((price, q))
        pct = Decimal(parsed["requested_discount_pct"])
        c = S.calc_quote(pairs, pct); auth = S.authorize_discount(pct, policy)
        for l, t in zip(lines, c["line_totals"]): l["line_total"] = str(t)
        return dict(lines=lines, subtotal=str(c["subtotal"]), discount_pct=str(pct), discount_amount=str(c["discount_amount"]),
                    total=str(c["total"]), needs_approval=auth["needs_approval"], required_role=auth["required_role"],
                    explanation=auth["reason"])


@dataclass
class Agents:
    sales: SalesAgent
    inventory: InventoryAgent
    commercial: CommercialAgent

    @classmethod
    def mock(cls): return cls(MockSalesAgent(), MockInventoryAgent(), MockCommercialAgent())
