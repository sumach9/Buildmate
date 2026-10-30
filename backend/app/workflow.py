"""Workflow coordinator + explicit state machine. Every transition and decision is audited."""
from __future__ import annotations
import uuid
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from pydantic import ValidationError
from . import services as S
from .agents import Agents
from .models import *
from .schemas import ParsedRequest, FulfillmentResult, QuoteDraft

TRANSITIONS = {
    "RECEIVED": {"VALIDATING_REQUEST", "FAILED"},
    "VALIDATING_REQUEST": {"NEEDS_CLARIFICATION", "CHECKING_INVENTORY", "FAILED"},
    "NEEDS_CLARIFICATION": {"VALIDATING_REQUEST"},
    "CHECKING_INVENTORY": {"CUSTOMER_DECISION_REQUIRED", "NEEDS_HUMAN_REVIEW", "PREPARING_QUOTE", "FAILED"},
    "CUSTOMER_DECISION_REQUIRED": {"PREPARING_QUOTE", "NEEDS_HUMAN_REVIEW", "REJECTED"},
    "NEEDS_HUMAN_REVIEW": {"CUSTOMER_DECISION_REQUIRED", "CHECKING_INVENTORY", "REJECTED"},
    "PREPARING_QUOTE": {"PENDING_APPROVAL", "APPROVED", "FAILED"},
    "PENDING_APPROVAL": {"APPROVED", "REJECTED"},
    "APPROVED": {"COMPLETED", "NEEDS_HUMAN_REVIEW"},
    "FAILED": {"VALIDATING_REQUEST", "CHECKING_INVENTORY", "PREPARING_QUOTE"},
    "REJECTED": set(), "COMPLETED": set(),
}
STATES = list(TRANSITIONS)
ROLES = ["viewer", "customer", "sales_rep", "manager"]


class WorkflowError(Exception):
    def __init__(self, msg, status=409): super().__init__(msg); self.status = status
class AgentOutputError(Exception): pass


class Coordinator:
    def __init__(self, db, agents: Agents, today: date, policy=S.DEFAULT_POLICY):
        self.db, self.agents, self.today, self.policy = db, agents, today, policy

    # ---- infrastructure ----
    def audit(self, r, event, actor="system", frm=None, to=None, **details):
        self.db.add(AuditEvent(request_id=r.id, event_type=event, actor=actor, from_state=frm, to_state=to, details=details))
    def _to(self, r, new, actor="system", **d):
        if new not in TRANSITIONS[r.state]: raise WorkflowError(f"Illegal transition {r.state} -> {new}")
        old, r.state = r.state, new
        self.audit(r, "STATE_CHANGE", actor, old, new, **d); self.db.flush()
    def _require(self, r, *states):
        if r.state not in states: raise WorkflowError(f"Action not allowed in state {r.state}.")

    # ---- public operations ----
    def create(self, text: str, customer_id: int | None, key: str | None = None):
        key = key or S.request_key(customer_id, text)
        if existing := self.db.scalar(select(WorkflowRequest).where(WorkflowRequest.idempotency_key == key)):
            return existing, False
        r = WorkflowRequest(workflow_id=str(uuid.uuid4()), idempotency_key=key, text=text, customer_id=customer_id)
        self.db.add(r); self.db.flush()
        self.audit(r, "REQUEST_RECEIVED", "customer", None, "RECEIVED", text=text)
        self._advance(r); return r, True

    def clarify(self, r, answer: str, customer_id: int | None, actor="customer"):
        self._require(r, "NEEDS_CLARIFICATION")
        r.text = f"{r.text} {answer}".strip()
        if customer_id: r.customer_id = customer_id
        self.audit(r, "CLARIFICATION_PROVIDED", actor, answer=answer)
        self._to(r, "VALIDATING_REQUEST", actor); self._advance(r); return r

    def retry(self, r, actor="sales_rep"):
        self._require(r, "FAILED")
        target = r.failed_from or "VALIDATING_REQUEST"
        self.audit(r, "RETRY", actor, retry_from=target); r.error = None
        self._to(r, target, actor); self._advance(r); return r

    def decide(self, r, decisions: dict[str, str], actor="customer"):
        self._require(r, "CUSTOMER_DECISION_REQUIRED")
        ful = r.fulfillment; chosen = {}
        for line in ful["lines"]:
            if line["shortage"] == 0: continue
            d = decisions.get(line["sku"])
            if not d: raise WorkflowError(f"Decision required for {line['sku']}.", 422)
            opt = next((o for o in line["options"] if o["id"] == d), None)
            if opt is None: raise WorkflowError(f"Option {d} is not offered for {line['sku']}.", 422)
            if d != "MANUAL_REVIEW" and opt["feasibility"] != "FEASIBLE":
                raise WorkflowError(f"Option {d} for {line['sku']} is {opt['feasibility']} and cannot be selected.", 422)
            chosen[line["sku"]] = d
        r.decisions = chosen; self.audit(r, "CUSTOMER_DECISION", actor, decisions=chosen)
        if "MANUAL_REVIEW" in chosen.values(): self._to(r, "NEEDS_HUMAN_REVIEW", actor)
        else: self._to(r, "PREPARING_QUOTE", actor); self._advance(r)
        return r

    def review(self, r, action: str, actor: str):
        self._require(r, "NEEDS_HUMAN_REVIEW")
        if action == "REOPEN_DECISION":
            self.audit(r, "HUMAN_REVIEW_RESOLVED", actor, action=action)
            self._to(r, "CUSTOMER_DECISION_REQUIRED", actor)  # customer re-chooses among feasible options
        elif action == "REJECT": self._to(r, "REJECTED", actor)
        else: raise WorkflowError("Unknown review action.", 422)
        return r

    def resolve_approval(self, r, approval: Approval, approve: bool, role: str, user: str, comment: str | None):
        if role != approval.required_role:
            self.audit(r, "APPROVAL_DENIED_UNAUTHORIZED", user, role=role, required=approval.required_role)
            self.db.commit(); raise WorkflowError(f"Role '{role}' may not resolve this approval (requires {approval.required_role}).", 403)
        if approval.status != "PENDING": raise WorkflowError(f"Approval already {approval.status}.")
        self._require(r, "PENDING_APPROVAL")
        quote = self.db.get(Quote, approval.quote_id)
        approval.status = "APPROVED" if approve else "REJECTED"; approval.resolved_by = user
        approval.comment = comment; approval.resolved_at = now()
        quote.status = "APPROVED" if approve else "REJECTED"
        self.audit(r, "APPROVAL_RESOLVED", user, decision=approval.status, comment=comment, quote=quote.quote_number)
        self._to(r, "APPROVED" if approve else "REJECTED", user); return r

    def finalize(self, r, role: str, user: str):
        if role not in ("sales_rep", "manager"):
            self.audit(r, "FINALIZE_DENIED_UNAUTHORIZED", user, role=role); self.db.commit()
            raise WorkflowError(f"Role '{role}' may not finalize.", 403)
        quote = self.db.scalar(select(Quote).where(Quote.request_id == r.id, Quote.status.in_(["APPROVED", "DRAFT", "REJECTED"])))
        if quote is None: raise WorkflowError("No quote to finalize.")
        approval = self.db.scalar(select(Approval).where(Approval.quote_id == quote.id))
        errs = S.validate_finalization(self.db, r, quote, approval, self.policy)
        if errs:
            self.audit(r, "FINALIZATION_BLOCKED", user, errors=errs); self.db.flush()
            if r.state == "APPROVED" and any("changed" in e or "missing" in e.lower() for e in errs):
                self._to(r, "NEEDS_HUMAN_REVIEW", user, reason="revalidation failed")
            self.db.commit(); raise WorkflowError("Finalization blocked: " + " ".join(errs))
        for l in quote.lines:  # commit: reserve stock
            inv = self.db.scalar(select(Inventory).where(Inventory.product_id == S.get_product(self.db, l["sku"]).id))
            inv.reserved += l["quantity"]
        quote.status = "FINAL"; self.audit(r, "FINALIZED", user, quote=quote.quote_number)
        self._to(r, "COMPLETED", user); return r

    # ---- internals ----
    def _advance(self, r):
        try:
            while True:
                if r.state == "RECEIVED": self._to(r, "VALIDATING_REQUEST")
                elif r.state == "VALIDATING_REQUEST": self._validate(r)
                elif r.state == "CHECKING_INVENTORY": self._inventory(r)
                elif r.state == "PREPARING_QUOTE": self._quote(r)
                else: break
        except Exception as e:  # agent/service failure => FAILED, retryable
            frm = r.state; r.failed_from = frm; r.error = f"{type(e).__name__}: {e}"
            self.audit(r, "ERROR", "system", step=frm, error=r.error)
            if frm in TRANSITIONS and "FAILED" in TRANSITIONS[frm]: self._to(r, "FAILED")
            else: raise
        self.db.flush()

    def _validate(self, r):
        raw = self.agents.sales.run(self.db, r.text, r.customer_id, self.today)
        try: p = ParsedRequest.model_validate(raw)
        except ValidationError as e: raise AgentOutputError(f"Sales agent output invalid: {e.error_count()} error(s): {e.errors()[0]['msg']}")
        self.audit(r, "AGENT_SALES", "sales_agent", output=p.model_dump(mode="json"))
        errs = list(p.validation_errors)
        if p.status == "COMPLETE":  # business-rule check of untrusted output
            if S.find_customer(self.db, p.customer_id) is None: raise AgentOutputError("Sales agent referenced a nonexistent customer.")
            for l in p.line_items:
                if not l.sku or S.get_product(self.db, l.sku) is None: raise AgentOutputError(f"Sales agent referenced unknown SKU {l.sku!r}.")
            if not p.line_items: raise AgentOutputError("COMPLETE request has no line items.")
        r.parsed = p.model_dump(mode="json")
        if p.status == "NEEDS_CLARIFICATION" or errs:
            self._to(r, "NEEDS_CLARIFICATION"); return
        r.customer_id = p.customer_id; self._to(r, "CHECKING_INVENTORY")

    def _inventory(self, r):
        raw = self.agents.inventory.run(self.db, r.parsed, self.today, self.policy)
        try: f = FulfillmentResult.model_validate(raw)
        except ValidationError as e: raise AgentOutputError(f"Inventory agent output invalid: {e.errors()[0]['msg']}")
        for l in f.lines:  # never trust agent-reported stock
            prod = S.get_product(self.db, l.sku)
            if prod is None or l.available != S.available(self.db, prod.id) or l.shortage != S.shortage(l.requested, l.available):
                raise AgentOutputError(f"Inventory agent figures for {l.sku} disagree with deterministic services.")
        r.fulfillment = f.model_dump(mode="json")
        self.audit(r, "AGENT_INVENTORY", "inventory_agent", output=r.fulfillment)
        if f.requires_human_review: self._to(r, "NEEDS_HUMAN_REVIEW")
        elif f.requires_customer_decision: self._to(r, "CUSTOMER_DECISION_REQUIRED")
        else: r.decisions = {}; self._to(r, "PREPARING_QUOTE")

    def _quote(self, r):
        raw = self.agents.commercial.run(self.db, r.parsed, r.fulfillment, r.decisions or {}, self.policy)
        try: d = QuoteDraft.model_validate(raw)
        except ValidationError as e: raise AgentOutputError(f"Commercial agent output invalid: {e.errors()[0]['msg']}")
        # recompute deterministically; agent arithmetic/prices are never trusted
        pairs = []
        for l in d.lines:
            if S.get_price(self.db, S.get_product(self.db, l.sku).id) != l.unit_price: raise AgentOutputError(f"Unapproved price for {l.sku}.")
            pairs.append((l.unit_price, l.quantity))
        c = S.calc_quote(pairs, d.discount_pct); auth = S.authorize_discount(d.discount_pct, self.policy)
        if c["total"] != d.total or c["subtotal"] != d.subtotal or auth["needs_approval"] != d.needs_approval:
            raise AgentOutputError("Commercial agent totals/approval flag disagree with deterministic services.")
        lines = [l.model_dump(mode="json") for l in d.lines]
        key = S.quote_key(r.workflow_id, lines, d.discount_pct)
        quote = self.db.scalar(select(Quote).where(Quote.idempotency_key == key))
        if quote is None:  # idempotent: retries never create duplicates
            quote = Quote(quote_number=f"Q-{uuid.uuid4().hex[:8].upper()}", request_id=r.id, idempotency_key=key, lines=lines,
                          subtotal=c["subtotal"], discount_pct=d.discount_pct, discount_amount=c["discount_amount"], total=c["total"])
            self.db.add(quote); self.db.flush()
            self.audit(r, "QUOTE_DRAFTED", "commercial_agent", quote=quote.quote_number, total=str(c["total"]))
            if auth["needs_approval"]:
                self.db.add(Approval(quote_id=quote.id, request_id=r.id, required_role=auth["required_role"], reason=auth["reason"]))
                self.audit(r, "APPROVAL_REQUESTED", "commercial_agent", reason=auth["reason"])
        quote.status = "DRAFT" if auth["needs_approval"] else "APPROVED"
        self._to(r, "PENDING_APPROVAL" if auth["needs_approval"] else "APPROVED")
