# BuildMate AI — multi-agent construction supply demo

One resumable workflow: **Sales Intelligence → Inventory & Fulfillment → Commercial & Quote**, coordinated by an explicit state machine with deterministic business services, human approvals and a full audit trail. Mock agents run with no API keys.

## Install & run
```bash
# backend (Python 3.11+)
cd backend && pip install -r requirements.txt
python -m app.seed                          # reproducible seed (drops/recreates buildmate.db)
uvicorn app.asgi:app --port 8000            # re-seeds on start; OpenAPI docs: http://localhost:8000/docs
# frontend (Node 18+), second terminal
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api to :8000)
```
## Tests
```bash
cd backend && python -m pytest -q     # 26 tests, business-outcome assertions
cd frontend && npm run build && npm test   # type-check + build + formatting unit test
```
## Demo (UI)
1. **New Request** → “Load demo request”, customer ABC Construction → *Submit*. Parsed: 500 cement, 200 drywall, 100 steel studs, Friday (2026-10-30 in tests), 10% requested.
2. **Decision Center** → steel stud shortage of 25 (75 available). Options: PARTIAL (feasible), SUBSTITUTE (approved heavy-gauge stud), DELAYED (**UNKNOWN**, no supplier date, not selectable), MANUAL_REVIEW. Requested subtotal $7,700.00. Choose PARTIAL → submit.
3. **Quotes & Approvals** → draft quote subtotal $7,537.50, 10% discount, total $6,783.75, status PENDING_APPROVAL. Switch role to `sales_rep`, click Approve → rejected (403, audited). Switch to `manager` → Approve.
4. Click **Finalize** (revalidates inventory/prices, reserves stock) → COMPLETED. Audit history shows every step.

## Architecture (`backend/app`)
> **Detailed Specification:** See [ARCHITECTURE.md](ARCHITECTURE.md) for deep-dive specifications, option scoring math, and full phase-by-phase breakdown.

### 1. High-Level System Architecture

```mermaid
flowchart TD
    subgraph UI["1. Presentation Layer (Vite + React 18)"]
        Client["Browser Client (:5174 / :5173)"]
        RoleSim["Role Switcher (viewer / customer / sales_rep / manager)"]
        Views["Views: Intake Form | Decision Center | Quotes & Approvals | Audit Trail"]
    end

    subgraph API["2. API & Routing Layer (FastAPI)"]
        FastAPIApp["FastAPI Server (:8000)"]
        CORS["CORS & Headers (X-Role, X-User, Idempotency-Key)"]
        Endpoints["REST API Endpoints (/api/requests, /api/quotes, /api/approvals)"]
    end

    subgraph Core["3. Orchestration & State Machine"]
        Coord["Workflow Coordinator (Coordinator)"]
        StateMachine["State Transition Engine (11 explicit states)"]
        AuditEngine["Audit Logger (every state change & action recorded)"]
    end

    subgraph Agents["4. Multi-Agent Layer (Untrusted Outputs)"]
        SalesAg["Sales Intelligence Agent (Extracts SKUs, qty, dates, requested discounts)"]
        InvAg["Inventory & Fulfillment Agent (Evaluates shortages, options & feasibility)"]
        CommAg["Commercial & Pricing Agent (Drafts quotes & discount proposals)"]
    end

    subgraph Services["5. Deterministic Business Services (Single Source of Truth)"]
        StockSvc["Inventory Service (on_hand - reserved)"]
        CompatSvc["Compatibility Service (approved-only substitutes)"]
        TransitSvc["Delivery & Transit Feasibility Engine"]
        ScoringSvc["Option Evaluation (weighted scoring: 40/30/20/10)"]
        PriceSvc["Pricing Book & Decimal Math (ROUND_HALF_UP)"]
        PolicySvc["Discount Policy Engine (authorization thresholds)"]
        FinalizeSvc["Finalization Revalidation & Stock Locking"]
    end

    subgraph Data["6. Persistence Layer (SQLAlchemy 2.0 + SQLite)"]
        DB[("buildmate.db")]
        Tables["Tables: requests, quotes, approvals, audit_events, products, inventory, customers, price_books"]
    end

    Client -->|HTTP / JSON| FastAPIApp
    FastAPIApp --> Endpoints
    Endpoints --> Coord
    Coord --> StateMachine
    Coord --> AuditEngine
    AuditEngine --> DB

    Coord <-->|Delegates tasks| Agents
    Coord <-->|Strictly verifies & recalculates against| Services
    Services <--> DB
    Coord --> DB
```

### 2. Workflow State Machine Lifecycle

Every state transition is validated against an explicit transition table and recorded in the audit event log:

```mermaid
stateDiagram-v2
    [*] --> RECEIVED: New Request Submitted
    RECEIVED --> VALIDATING_REQUEST: Coordinator advance

    VALIDATING_REQUEST --> NEEDS_CLARIFICATION: Missing info / ambiguous SKU
    NEEDS_CLARIFICATION --> VALIDATING_REQUEST: Customer provides clarification

    VALIDATING_REQUEST --> CHECKING_INVENTORY: Parsed & validated
    VALIDATING_REQUEST --> FAILED: Parse/Validation Error

    CHECKING_INVENTORY --> CUSTOMER_DECISION_REQUIRED: Shortage detected (Feasible options available)
    CHECKING_INVENTORY --> NEEDS_HUMAN_REVIEW: Manual review needed / lead-time unknown
    CHECKING_INVENTORY --> PREPARING_QUOTE: All items fully available in stock
    CHECKING_INVENTORY --> FAILED: Service Error

    CUSTOMER_DECISION_REQUIRED --> PREPARING_QUOTE: Customer selects Partial / Substitute / Delayed
    CUSTOMER_DECISION_REQUIRED --> NEEDS_HUMAN_REVIEW: Customer requests Manual Review
    CUSTOMER_DECISION_REQUIRED --> REJECTED: Customer cancels

    NEEDS_HUMAN_REVIEW --> CUSTOMER_DECISION_REQUIRED: Sales rep reopens decision
    NEEDS_HUMAN_REVIEW --> REJECTED: Sales rep rejects request

    PREPARING_QUOTE --> PENDING_APPROVAL: Discount exceeds policy threshold (> 5%)
    PREPARING_QUOTE --> APPROVED: Discount within policy (Auto-approved)
    PREPARING_QUOTE --> FAILED: Calculation Error

    PENDING_APPROVAL --> APPROVED: Manager approves quote
    PENDING_APPROVAL --> REJECTED: Manager rejects quote

    APPROVED --> COMPLETED: Finalize order (Stock locked & reserved)
    APPROVED --> NEEDS_HUMAN_REVIEW: Finalization revalidation fails (Stock/Price drift)

    FAILED --> VALIDATING_REQUEST: Retry action
    FAILED --> CHECKING_INVENTORY: Retry action
    FAILED --> PREPARING_QUOTE: Retry action

    REJECTED --> [*]
    COMPLETED --> [*]
```

### 3. Agent Execution & Validation Pattern

A fundamental design rule in BuildMate AI is that **Agent Output is Untrusted**. The AI suggests and parses; deterministic domain services verify and calculate:

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Sales Rep
    participant Coord as Coordinator
    participant Agent as Specialized Agent (LLM / Mock)
    participant Validator as Pydantic Schema
    participant Service as Deterministic Services
    participant DB as SQLite DB

    User->>Coord: Advance Workflow Step
    Coord->>Agent: Prompt / Run Agent with Context
    Agent-->>Coord: Raw Output (JSON / Dict)
    
    Note over Coord,Validator: Step 1: Structural Validation
    Coord->>Validator: model_validate(raw)
    Validator-->>Coord: Validated Pydantic Model
    
    Note over Coord,Service: Step 2: Deterministic Business Cross-Check
    Coord->>Service: Verify stock / Recalculate price & discount
    Service->>DB: Query PriceBook & Inventory
    DB-->>Service: Authoritative Database Data
    Service-->>Coord: Recomputed Values

    alt Agent output does NOT match Service truth
        Coord->>Coord: Raise AgentOutputError & transition to FAILED / NEEDS_REVIEW
    else Verified & Match
        Coord->>DB: Update Request State & Record AuditEvent
        Coord-->>User: Next State (Decision Required / Approval / Complete)
    end
```

### 4. Codebase Components (`backend/app`)
- `models.py` SQLAlchemy models; money stored as strings, handled as `Decimal` (`ROUND_HALF_UP`, cents).
- `services.py` deterministic services: lookup, availability (on-hand − reserved), shortage, approved-only compatibility, delivery feasibility, option evaluation (weights 40/30/20/10; INFEASIBLE hard-excluded; missing data ⇒ UNKNOWN), pricing, quote math, discount authorization (`DEFAULT_POLICY`, configurable via `create_app(policy=…)`), finalization validation, idempotency keys.
- `agents.py` three agent interfaces, deterministic mocks, and a provider-neutral `ModelAdapter` / `LLMSalesAgent` example.
- `workflow.py` coordinator + transition table. Agent output is **untrusted**: Pydantic-validated, then cross-checked against services (stock, prices, totals, approval flag).
- `main.py` REST API (see `/docs`). Simulated roles via `X-Role` (`viewer|customer|sales_rep|manager`) and `X-User` headers; approvals need `manager`.
- Idempotency: request key = hash(customer, text) or `Idempotency-Key` header; quote key = hash(workflow, lines, discount). Replayed decisions return 409.

## Limitations / next steps
Roles are header-simulated (no real auth). Mock parser is regex/keyword based; plug a real model into `ModelAdapter` (output is already validated). Delivery feasibility covers WA stock only; supplier lead times are intentionally unknown in seed data. No tax/delivery fees. Single warehouse in seed. Frontend has a minimal unit test and no browser/E2E tests; the UI was type-checked and built but not visually tested in a browser. SQLite only.
