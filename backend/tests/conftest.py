import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from datetime import date
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.models import make_session_factory
from app.seed import seed
from app.agents import Agents

TODAY = date(2026, 10, 28)  # Wednesday; "Friday" == 2026-10-30
DEMO = ("We are building a commercial property in Bellevue. I need 500 bags of cement, 200 drywall sheets, "
        "and 100 steel studs delivered by Friday. Give me your best price, preferably with a 10% discount.")
H = lambda role, user=None: {"X-Role": role, "X-User": user or f"{role}-demo"}


class Ctx:
    def __init__(self, agents=None):
        self.sf = make_session_factory("sqlite://"); seed(self.sf)
        self.app = create_app(self.sf, agents, today_fn=lambda: TODAY)
        self.c = TestClient(self.app)
    def new(self, text=DEMO, cid=1, role="customer", **kw): return self.c.post("/api/requests", json={"text": text, "customer_id": cid}, headers={**H(role), **kw})
    def get(self, rid): return self.c.get(f"/api/requests/{rid}").json()
    def decide(self, rid, d, role="customer"): return self.c.post(f"/api/requests/{rid}/decisions", json={"decisions": d}, headers=H(role))
    def resolve(self, aid, decision, role="manager"): return self.c.post(f"/api/approvals/{aid}/resolve", json={"decision": decision}, headers=H(role))
    def finalize(self, rid, role="sales_rep"): return self.c.post(f"/api/requests/{rid}/finalize", headers=H(role))
    def db(self): return self.sf()
    def avail(self, sku):
        from app import services as S
        d = self.sf(); v = S.available(d, S.get_product(d, sku).id); d.close(); return v

@pytest.fixture
def ctx(): return Ctx()
@pytest.fixture
def make_ctx(): return Ctx
