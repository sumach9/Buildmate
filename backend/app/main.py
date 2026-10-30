"""FastAPI app. Simulated roles are passed via X-Role / X-User headers (viewer, customer, sales_rep, manager)."""
from __future__ import annotations
import os
from datetime import date
from typing import Literal
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from .agents import Agents
from .models import *
from .seed import seed
from .services import DEFAULT_POLICY
from . import services as S
from .workflow import Coordinator, WorkflowError, ROLES, STATES, TRANSITIONS


class NewRequest(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    customer_id: int | None = None
class Clarify(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    customer_id: int | None = None
class Decisions(BaseModel):
    decisions: dict[str, Literal["PARTIAL", "SUBSTITUTE", "DELAYED", "MANUAL_REVIEW"]]
class Resolve(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    comment: str | None = Field(default=None, max_length=1000)
class Review(BaseModel):
    action: Literal["REOPEN_DECISION", "REJECT"]


def create_app(session_factory=None, agents: Agents | None = None, today_fn=date.today, policy=DEFAULT_POLICY, auto_seed=False):
    app = FastAPI(title="BuildMate AI", version="1.0", description="Multi-agent construction supply workflow (demo). "
                  "Simulated roles via X-Role header: " + ", ".join(ROLES))
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
    sf = session_factory or make_session_factory(os.getenv("BUILDMATE_DB", "sqlite:///./buildmate.db"))
    if auto_seed: seed(sf)
    app.state.sf, app.state.agents = sf, agents or Agents.mock()

    def get_db():
        db = sf()
        try: yield db
        finally: db.close()
    def ident(x_role: str = Header("viewer"), x_user: str = Header("demo-user")):
        if x_role not in ROLES: raise HTTPException(400, f"Unknown role {x_role}")
        return x_role, x_user
    def coord(db=Depends(get_db)): return Coordinator(db, app.state.agents, today_fn(), policy)
    def need(role_set, who):
        if who[0] not in role_set: raise HTTPException(403, f"Role '{who[0]}' not permitted; requires one of {sorted(role_set)}")

    @app.exception_handler(WorkflowError)
    async def wf_err(_: Request, e: WorkflowError): return JSONResponse({"detail": str(e)}, status_code=e.status)

    def get_req(db, rid):
        r = db.get(WorkflowRequest, rid)
        if not r: raise HTTPException(404, "Request not found")
        return r
    def quote_dict(q: Quote, db):
        a = db.scalar(select(Approval).where(Approval.quote_id == q.id))
        return dict(id=q.id, quote_number=q.quote_number, request_id=q.request_id, status=q.status, lines=q.lines,
                    subtotal=str(q.subtotal), discount_pct=str(q.discount_pct), discount_amount=str(q.discount_amount),
                    total=str(q.total), approval=approval_dict(a) if a else None, created_at=q.created_at.isoformat())
    def approval_dict(a: Approval):
        return dict(id=a.id, quote_id=a.quote_id, request_id=a.request_id, status=a.status, required_role=a.required_role,
                    reason=a.reason, resolved_by=a.resolved_by, comment=a.comment)
    def req_dict(r, db, detail=True):
        d = dict(id=r.id, workflow_id=r.workflow_id, text=r.text, customer_id=r.customer_id, state=r.state, error=r.error,
                 failed_from=r.failed_from, created_at=r.created_at.isoformat(), updated_at=r.updated_at.isoformat(),
                 retry_eligible=r.state == "FAILED")
        if detail:
            qs = db.scalars(select(Quote).where(Quote.request_id == r.id).order_by(Quote.id)).all()
            ev = db.scalars(select(AuditEvent).where(AuditEvent.request_id == r.id).order_by(AuditEvent.id)).all()
            d.update(parsed=r.parsed, fulfillment=r.fulfillment, decisions=r.decisions,
                     quotes=[quote_dict(q, db) for q in qs], audit=[audit_dict(e) for e in ev])
        return d
    def audit_dict(e): return dict(id=e.id, ts=e.ts.isoformat(), event_type=e.event_type, actor=e.actor, from_state=e.from_state, to_state=e.to_state, details=e.details)

    @app.get("/api/meta")
    def meta(): return dict(roles=ROLES, states=STATES, policy={k: str(v) for k, v in policy.items() if k.endswith("pct")})
    @app.get("/api/customers")
    def customers(db=Depends(get_db)): return [dict(id=c.id, name=c.name, city=c.city, state=c.state) for c in db.scalars(select(Customer))]
    @app.get("/api/products")
    def products(db=Depends(get_db)):
        return [dict(sku=p.sku, name=p.name, on_hand=sum(i.on_hand for i in db.scalars(select(Inventory).where(Inventory.product_id == p.id))),
                     reserved=sum(i.reserved for i in db.scalars(select(Inventory).where(Inventory.product_id == p.id))),
                     available=S.available(db, p.id), price=str(S.get_price(db, p.id))) for p in db.scalars(select(Product))]

    @app.post("/api/requests", status_code=201)
    def create_request(body: NewRequest, who=Depends(ident), c: Coordinator = Depends(coord), idempotency_key: str | None = Header(None)):
        need({"customer", "sales_rep", "manager"}, who)
        r, created = c.create(body.text, body.customer_id, idempotency_key)
        c.db.commit(); out = req_dict(r, c.db); out["duplicate"] = not created; return out
    @app.get("/api/requests")
    def list_requests(state: str | None = None, db=Depends(get_db)):
        q = select(WorkflowRequest).order_by(WorkflowRequest.id.desc())
        if state: q = q.where(WorkflowRequest.state == state)
        return [req_dict(r, db, False) for r in db.scalars(q)]
    @app.get("/api/requests/{rid}")
    def get_request(rid: int, db=Depends(get_db)): return req_dict(get_req(db, rid), db)
    @app.get("/api/requests/{rid}/audit")
    def get_audit(rid: int, db=Depends(get_db)):
        get_req(db, rid); return [audit_dict(e) for e in db.scalars(select(AuditEvent).where(AuditEvent.request_id == rid).order_by(AuditEvent.id))]
    @app.get("/api/requests/{rid}/status")
    def get_status(rid: int, db=Depends(get_db)):
        r = get_req(db, rid); return dict(id=r.id, state=r.state, error=r.error, retry_eligible=r.state == "FAILED", allowed_next=sorted(TRANSITIONS[r.state]))

    def act(rid, fn, c):
        r = get_req(c.db, rid)
        try: fn(r)
        except WorkflowError: c.db.rollback(); raise
        c.db.commit(); return req_dict(r, c.db)
    @app.post("/api/requests/{rid}/clarify")
    def clarify(rid: int, b: Clarify, who=Depends(ident), c=Depends(coord)):
        need({"customer", "sales_rep", "manager"}, who); return act(rid, lambda r: c.clarify(r, b.answer, b.customer_id, who[0]), c)
    @app.post("/api/requests/{rid}/decisions")
    def decisions(rid: int, b: Decisions, who=Depends(ident), c=Depends(coord)):
        need({"customer", "sales_rep", "manager"}, who); return act(rid, lambda r: c.decide(r, b.decisions, who[0]), c)
    @app.post("/api/requests/{rid}/retry")
    def retry(rid: int, who=Depends(ident), c=Depends(coord)):
        need({"sales_rep", "manager"}, who); return act(rid, lambda r: c.retry(r, who[0]), c)
    @app.post("/api/requests/{rid}/review")
    def review(rid: int, b: Review, who=Depends(ident), c=Depends(coord)):
        need({"manager"}, who); return act(rid, lambda r: c.review(r, b.action, who[0]), c)
    @app.post("/api/requests/{rid}/finalize")
    def finalize(rid: int, who=Depends(ident), c=Depends(coord)):
        return act(rid, lambda r: c.finalize(r, who[0], x_user_of(who)), c)
    def x_user_of(who): return who[1]

    @app.get("/api/quotes")
    def list_quotes(db=Depends(get_db)): return [quote_dict(q, db) for q in db.scalars(select(Quote).order_by(Quote.id.desc()))]
    @app.get("/api/quotes/{qid}")
    def get_quote(qid: int, db=Depends(get_db)):
        q = db.get(Quote, qid)
        if not q: raise HTTPException(404, "Quote not found")
        return quote_dict(q, db)
    @app.get("/api/approvals")
    def list_approvals(status: str | None = None, db=Depends(get_db)):
        q = select(Approval).order_by(Approval.id.desc())
        if status: q = q.where(Approval.status == status)
        return [approval_dict(a) for a in db.scalars(q)]
    @app.post("/api/approvals/{aid}/resolve")
    def resolve(aid: int, b: Resolve, who=Depends(ident), c=Depends(coord)):
        a = c.db.get(Approval, aid)
        if not a: raise HTTPException(404, "Approval not found")
        r = get_req(c.db, a.request_id)
        return act(r.id, lambda r: c.resolve_approval(r, a, b.decision == "APPROVE", who[0], who[1], b.comment), c)

    @app.get("/api/dashboard")
    def dashboard(db=Depends(get_db)):
        reqs = db.scalars(select(WorkflowRequest)).all()
        open_ = [r for r in reqs if r.state not in ("COMPLETED", "REJECTED")]
        exc = [r for r in reqs if r.fulfillment and any(l["shortage"] > 0 for l in r.fulfillment["lines"]) and r.state != "REJECTED"]
        ev = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(12)).all()
        return dict(open_requests=len(open_), draft_quotes=db.scalar(select(func.count()).select_from(Quote).where(Quote.status == "DRAFT")),
                    pending_approvals=db.scalar(select(func.count()).select_from(Approval).where(Approval.status == "PENDING")),
                    inventory_exceptions=len(exc), by_state={s: sum(r.state == s for r in reqs) for s in STATES if any(r.state == s for r in reqs)},
                    recent_activity=[audit_dict(e) for e in ev])
    return app

