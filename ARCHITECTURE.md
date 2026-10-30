# BuildMate AI — System Architecture & Design Specification

BuildMate AI is a multi-agent workflow platform for construction supply ordering, fulfillment, and commercial quote generation. It coordinates autonomous agents with deterministic business validation, human-in-the-loop approvals, and full auditability.

---

## 1. High-Level System Architecture

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

---

## 2. Workflow State Machine Lifecycle

The coordinator operates on an explicit state machine. Each transition is validated against `TRANSITIONS` and audited:

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

---

## 3. Agent Execution & Validation Pattern

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

---

## 4. Key Architectural Pillars

| Component | Responsibility | Guardrails & Design Rules |
| :--- | :--- | :--- |
| **Untrusted Agent Outputs** | Agents parse natural language, suggest substitution options, and propose discounts. | Agent calculations, inventory levels, and prices are **never trusted blindly**. They are strictly recomputed and verified against `services.py`. |
| **Deterministic Services** | Product catalog lookup, stock availability (`on_hand - reserved`), delivery lead times, option scoring, discount policy authorization. | Single source of truth. Handles edge cases gracefully (e.g. unknown supplier lead times mark option as `UNKNOWN`, not selectable). |
| **Explicit State Machine** | Controls allowable state transitions (`TRANSITIONS`). | Prevents illegal state jumps; allows retry from `FAILED` states. |
| **Full Audit Trail** | Tracks every state change, actor, and payload into `audit_events`. | Complete traceability for every decision, rejection, and authorization. |
| **Role-Based Access Control** | Header-simulated roles (`viewer`, `customer`, `sales_rep`, `manager`). | Approvals strictly require `manager`; finalization requires `sales_rep` or `manager`. |
| **Concurrency & Idempotency** | Prevents duplicate requests or quotes on network retry. | Deterministic hash keys (`idempotency_key`); finalization revalidates stock and pricing to prevent race conditions. |

---

## 5. Multi-Agent System & Phase-by-Phase Execution

BuildMate AI features **3 specialized agents** coordinated by the explicit state machine, ensuring autonomous reasoning with deterministic financial and inventory guardrails.

```mermaid
flowchart LR
    A["Phase 1<br><b>Sales Intelligence</b><br>(SalesAgent)"] --> B["Phase 2<br><b>Inventory & Fulfillment</b><br>(InventoryAgent)"]
    B --> C["Phase 3<br><b>Commercial & Quote</b><br>(CommercialAgent)"]
    C --> D["Phase 4<br><b>Approval & Finalization</b><br>(Human + Services)"]
```

### Agent Specification Matrix

| Agent | Active Workflow State | Input Data | Output / Responsibilities | Deterministic Guardrails |
| :--- | :--- | :--- | :--- | :--- |
| **1. Sales Intelligence Agent** (`SalesAgent`) | `VALIDATING_REQUEST` | Raw freeform request text, customer ID, current date | • Extracts structured line items, quantities, delivery deadline, and location.<br>• Identifies requested discounts.<br>• Formulates clarification questions if details or SKUs are ambiguous. | Pydantic schema validation (`ParsedRequest`). Checks that customer and all line-item SKUs exist in database catalog. |
| **2. Inventory & Fulfillment Agent** (`InventoryAgent`) | `CHECKING_INVENTORY` | Verified line items, deadline date, inventory policy | • Calculates net stock (`on_hand - reserved`).<br>• Identifies shortages.<br>• Evaluates 4 fulfillment strategies (Partial, Substitute, Delayed, Manual Review).<br>• Scores options with weighted ranking. | Validates against `FulfillmentResult`. Cross-checks agent's reported stock against live database inventory. Hallucinated stock numbers trigger an exception. |
| **3. Commercial & Pricing Agent** (`CommercialAgent`) | `PREPARING_QUOTE` | Chosen fulfillment lines, catalog prices, discount policy | • Resolves final line quantities based on customer decision.<br>• Looks up price book rates.<br>• Computes subtotal, discounts, and total price.<br>• Checks discount authorization policy. | Validates against `QuoteDraft`. Exact decimal arithmetic (`ROUND_HALF_UP`) re-calculated deterministically by `services.py`. Price changes or unapproved totals are rejected. |

---

### Detailed Phase-by-Phase Walkthrough

#### Phase 1: Intake & Sales Intelligence (`VALIDATING_REQUEST`)
* **Agent:** `SalesAgent` (`MockSalesAgent` or `LLMSalesAgent` via `ModelAdapter`).
* **Trigger:** Customer or sales rep submits freeform text.
* **Process:**
  1. Identifies customer account from text or input parameters.
  2. Parses product mentions and queries catalog keywords (`find_products`).
  3. Parses deadlines (e.g., relative dates like *"by Friday"* converted to ISO format using current date).
  4. Checks for ambiguities (e.g. "drywall" matching multiple sheet sizes).
* **Transitions:**
  * **Ambiguities / Missing details:** Transitions to **`NEEDS_CLARIFICATION`** (customer must answer clarifying questions).
  * **Complete & verified:** Transitions to **`CHECKING_INVENTORY`**.

#### Phase 2: Inventory & Fulfillment (`CHECKING_INVENTORY`)
* **Agent:** `InventoryAgent` (`MockInventoryAgent`).
* **Trigger:** Verified `ParsedRequest` provided by coordinator.
* **Process:**
  1. Computes live net stock: $\text{Available} = \text{On-Hand} - \text{Reserved}$.
  2. Calculates shortage: $\text{Shortage} = \max(0, \text{Requested} - \text{Available})$.
  3. Formulates up to **4 fulfillment options**:
     * **`PARTIAL`**: Ship available stock now by deadline.
     * **`SUBSTITUTE`**: Find compatible items (`approved=True` in `compatibility` table, e.g. heavy-gauge stud).
     * **`DELAYED`**: Source from supplier lead-time (marked `UNKNOWN` and unselectable if lead time is missing).
     * **`MANUAL_REVIEW`**: Escalate to dispatcher.
  4. Scores options using a 40/30/20/10 weighted model (Margin, Fulfillment Speed, Supplier Reliability, Customer Convenience).
* **Transitions:**
  * **All items available:** Advances directly to **`PREPARING_QUOTE`**.
  * **Shortage with feasible options:** Transitions to **`CUSTOMER_DECISION_REQUIRED`** (customer chooses in UI).
  * **No feasible options / complex shortage:** Transitions to **`NEEDS_HUMAN_REVIEW`**.

#### Phase 3: Commercial & Quote (`PREPARING_QUOTE`)
* **Agent:** `CommercialAgent` (`MockCommercialAgent`).
* **Trigger:** Stock confirmed or customer selected fulfillment options.
* **Process:**
  1. Resolves item quantities (original vs substitute SKUs).
  2. Looks up unit prices from active price books.
  3. Applies discount policy (`DEFAULT_POLICY`):
     * $\le 5\%$ discount: Within sales rep authority $\rightarrow$ **Auto-approved**.
     * $> 5\%$ discount: Exceeds standard authority $\rightarrow$ **Requires Manager Approval**.
     * Exceeds customer limit $\rightarrow$ **Rejected**.
* **Transitions:**
  * **Within policy ($\le 5\%$):** Transitions directly to **`APPROVED`**.
  * **Requires manager approval ($> 5\%$):** Creates an `Approval` record and transitions to **`PENDING_APPROVAL`**.

#### Phase 4: Human-in-the-Loop & Finalization (`APPROVED` $\rightarrow$ `COMPLETED`)
* **Agents:** **None** — this phase is strictly human roles and deterministic code.
* **Process:**
  1. **Manager Approval:** A user with `X-Role: manager` resolves pending approvals (`APPROVE` or `REJECT`).
  2. **Finalization (`finalize`):** A `sales_rep` or `manager` triggers finalization.
  3. **Concurrency & Drift Check:** Runs `validate_finalization()` to re-verify stock availability and price book rates, preventing race conditions.
  4. **Stock Reservation:** Locks inventory (`inv.reserved += quantity`), sets quote to `FINAL`, and transitions state to **`COMPLETED`**.


