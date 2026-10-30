import { useCallback, useEffect, useState } from "react";
import { api, DEMO_TEXT, money, Role } from "./api";

type Any = any;
const TABS = ["Dashboard", "New Request", "Agent Workflow", "Decision Center", "Quotes & Approvals"] as const;
type Tab = (typeof TABS)[number];
const AGENT_STEPS: [string, string][] = [["AGENT_SALES", "Agent 1 · Sales Intelligence"], ["AGENT_INVENTORY", "Agent 2 · Inventory & Fulfillment"], ["QUOTE_DRAFTED", "Agent 3 · Commercial & Quote"]];

function Status({ loading, error, ok }: { loading?: boolean; error?: string | null; ok?: string | null }) {
  return <>{loading && <div className="msg mut">Loading…</div>}{error && <div className="msg err">⚠ {error}</div>}{ok && <div className="msg ok">✓ {ok}</div>}</>;
}
const Badge = ({ s }: { s: string }) => <span className={`badge ${s}`}>{s.replaceAll("_", " ")}</span>;

export default function App() {
  const [tab, setTab] = useState<Tab>("Dashboard");
  const [role, setRole] = useState<Role>("sales_rep");
  const [sel, setSel] = useState<number | null>(null);
  const go = (t: Tab, id?: number) => { if (id != null) setSel(id); setTab(t); };
  return (<>
    <header><h1>🏗 BuildMate AI</h1>
      <nav>{TABS.map(t => <button key={t} className={t === tab ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}</nav>
      <label className="row">Simulated role
        <select style={{ width: 130 }} value={role} onChange={e => setRole(e.target.value as Role)}>
          {["viewer", "customer", "sales_rep", "manager"].map(r => <option key={r}>{r}</option>)}</select></label></header>
    <main>
      {tab === "Dashboard" && <Dashboard role={role} go={go} />}
      {tab === "New Request" && <NewRequest role={role} go={go} />}
      {tab === "Agent Workflow" && <Workflow role={role} sel={sel} setSel={setSel} />}
      {tab === "Decision Center" && <Decisions role={role} sel={sel} setSel={setSel} />}
      {tab === "Quotes & Approvals" && <Quotes role={role} sel={sel} setSel={setSel} />}
    </main></>);
}

function useLoad<T>(path: string, role: Role, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null); const [loading, setL] = useState(true); const [error, setE] = useState<string | null>(null);
  const reload = useCallback(() => { setL(true); api<T>(path, role).then(d => { setData(d); setE(null); }).catch(e => setE(e.message)).finally(() => setL(false)); }, [path, role]);
  useEffect(reload, [reload, ...deps]); return { data, loading, error, reload };
}

function Dashboard({ role, go }: { role: Role; go: (t: Tab, id?: number) => void }) {
  const d = useLoad<Any>("/dashboard", role); const reqs = useLoad<Any[]>("/requests", role); const inv = useLoad<Any[]>("/products", role);
  const k = d.data;
  return (<>
    <Status loading={d.loading} error={d.error} />
    {k && <div className="grid">{[["Open requests", k.open_requests], ["Draft quotes", k.draft_quotes], ["Pending approvals", k.pending_approvals], ["Inventory exceptions", k.inventory_exceptions]]
      .map(([l, v]) => <div className="card kpi" key={l as string}><b>{v}</b><span>{l}</span></div>)}</div>}
    <div className="card"><h2>Requests</h2>{reqs.data?.length === 0 && <p className="mut">No requests yet. Create one in “New Request”.</p>}
      <table><tbody>{reqs.data?.map(r => <tr key={r.id}><td>#{r.id}</td><td>{r.text.slice(0, 70)}…</td><td><Badge s={r.state} /></td>
        <td><button className="btn sec" onClick={() => go("Agent Workflow", r.id)}>Open</button></td></tr>)}</tbody></table></div>
    <div className="card"><h2>Inventory (on hand − reserved)</h2><table><thead><tr><th>SKU</th><th>Product</th><th className="n">On hand</th><th className="n">Reserved</th><th className="n">Available</th><th className="n">Price</th></tr></thead>
      <tbody>{inv.data?.map(p => <tr key={p.sku}><td>{p.sku}</td><td>{p.name}</td><td className="n">{p.on_hand}</td><td className="n">{p.reserved}</td><td className="n">{p.available}</td><td className="n">{money(p.price)}</td></tr>)}</tbody></table></div>
    <div className="card"><h2>Recent activity</h2><div className="tl">{k?.recent_activity.map((e: Any) => <div key={e.id}><b>#{e.id}</b> {e.event_type} <span className="mut">· {e.actor} {e.to_state ? "→ " + e.to_state : ""}</span></div>)}
      {k?.recent_activity.length === 0 && <span className="mut">No activity yet.</span>}</div></div></>);
}

function NewRequest({ role, go }: { role: Role; go: (t: Tab, id?: number) => void }) {
  const cust = useLoad<Any[]>("/customers", role); const [text, setText] = useState(DEMO_TEXT); const [cid, setCid] = useState<number | "">(1);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [res, setRes] = useState<Any>(null); const [ans, setAns] = useState("");
  const submit = async () => { setBusy(true); setErr(null); try { setRes(await api("/requests", role, { method: "POST", body: { text, customer_id: cid || null } })); } catch (e: Any) { setErr(e.message); } setBusy(false); };
  const clarify = async () => { setBusy(true); setErr(null); try { setRes(await api(`/requests/${res.id}/clarify`, role, { method: "POST", body: { answer: ans, customer_id: cid || null } })); setAns(""); } catch (e: Any) { setErr(e.message); } setBusy(false); };
  const p = res?.parsed;
  return (<div className="card"><h2>New request</h2>
    <label>Customer<select value={cid} onChange={e => setCid(e.target.value ? Number(e.target.value) : "")}><option value="">— none —</option>{cust.data?.map(c => <option key={c.id} value={c.id}>{c.name} ({c.city}, {c.state})</option>)}</select></label>
    <label>Request<textarea rows={5} value={text} onChange={e => setText(e.target.value)} /></label>
    <div className="row" style={{ marginTop: 8 }}><button className="btn" disabled={busy || !text.trim()} onClick={submit}>{busy ? "Processing…" : "Submit to agents"}</button>
      <button className="btn sec" onClick={() => setText(DEMO_TEXT)}>Load demo request</button></div>
    <Status error={err} />
    {res && <div style={{ marginTop: 12 }}><div className="row"><b>Request #{res.id}</b><Badge s={res.state} />{res.duplicate && <span className="mut">(duplicate — existing request returned)</span>}</div>
      {res.error && <Status error={res.error} />}
      {p && <><p className="mut">{p.explanation}</p>
        <table><thead><tr><th>Parsed text</th><th>SKU</th><th className="n">Qty</th></tr></thead><tbody>{p.line_items.map((l: Any, i: number) => <tr key={i}><td>{l.raw_text}</td><td>{l.sku ?? "AMBIGUOUS: " + l.candidates.join(", ")}</td><td className="n">{l.quantity}</td></tr>)}</tbody></table>
        <p>Delivery: {p.delivery_location ?? "?"} by {p.delivery_date ?? "?"} · Requested discount: {p.requested_discount_pct}% <span className="mut">(request only — policy decides)</span></p>
        {p.validation_errors.map((e: string) => <div className="msg err" key={e}>{e}</div>)}
        {p.clarification_questions.length > 0 && <div className="msg"><b>Clarification needed</b>{p.clarification_questions.map((q: string) => <div key={q}>• {q}</div>)}
          {res.state === "NEEDS_CLARIFICATION" && <div className="row" style={{ marginTop: 6 }}><input value={ans} onChange={e => setAns(e.target.value)} placeholder="Your answer (e.g. 'by Friday', 'steel studs')" /><button className="btn" disabled={!ans || busy} onClick={clarify}>Send</button></div>}</div>}</>}
      <button className="btn sec" style={{ marginTop: 8 }} onClick={() => go(res.state === "CUSTOMER_DECISION_REQUIRED" ? "Decision Center" : "Agent Workflow", res.id)}>Continue →</button></div>}</div>);
}

function useRequest(role: Role, sel: number | null, setSel: (n: number) => void) {
  const list = useLoad<Any[]>("/requests", role); const cur = useLoad<Any>(sel ? `/requests/${sel}` : "/meta", role, [sel]);
  useEffect(() => { if (sel == null && list.data?.length) setSel(list.data[0].id); }, [list.data, sel, setSel]);
  const picker = <label>Request<select value={sel ?? ""} onChange={e => setSel(Number(e.target.value))}>{list.data?.map(r => <option key={r.id} value={r.id}>#{r.id} · {r.state} · {r.text.slice(0, 50)}</option>)}</select></label>;
  const reloadAll = () => { list.reload(); cur.reload(); };
  return { picker, req: sel ? cur.data : null, loading: cur.loading || list.loading, error: cur.error || list.error, reloadAll, empty: list.data?.length === 0 };
}
async function act(role: Role, path: string, body: unknown | undefined, reload: () => void, setMsg: (m: { e?: string; ok?: string }) => void, ok: string) {
  try { await api(path, role, { method: "POST", body }); setMsg({ ok }); } catch (e: Any) { setMsg({ e: e.message }); } reload();
}

function Workflow({ role, sel, setSel }: { role: Role; sel: number | null; setSel: (n: number) => void }) {
  const { picker, req, loading, error, reloadAll, empty } = useRequest(role, sel, setSel); const [msg, setMsg] = useState<{ e?: string; ok?: string }>({});
  if (empty) return <div className="card">No requests yet.</div>;
  return (<><div className="card">{picker}<Status loading={loading} error={error} /><Status error={msg.e} ok={msg.ok} /></div>
    {req && <><div className="card"><div className="row"><h2 style={{ margin: 0 }}>Backend state</h2><Badge s={req.state} /><button className="btn sec" onClick={reloadAll}>Refresh</button>
      {req.retry_eligible && <button className="btn" onClick={() => act(role, `/requests/${req.id}/retry`, undefined, reloadAll, setMsg, "Retry executed")}>Retry failed step ({req.failed_from})</button>}</div>
      {req.error && <div className="msg err" style={{ marginTop: 8 }}>{req.error}</div>}
      <p className="mut">Workflow {req.workflow_id}</p></div>
      <div className="card"><h2>Agent outputs</h2>{AGENT_STEPS.map(([ev, label]) => { const e = [...req.audit].reverse().find((x: Any) => x.event_type === ev);
        return <div className="opt" key={ev}><b>{label}</b> {e ? <span className="mut">ran at {new Date(e.ts).toLocaleTimeString()}</span> : <span className="mut">not run yet</span>}
          {e && <pre>{ev === "AGENT_SALES" ? e.details.output.explanation : ev === "AGENT_INVENTORY" ? e.details.output.explanation : `Quote ${e.details.quote} total ${money(e.details.total)}`}</pre>}</div>; })}</div>
      <div className="card"><h2>State machine timeline</h2><div className="tl">{req.audit.map((e: Any) => <div key={e.id}><span className="mut">{new Date(e.ts).toLocaleTimeString()}</span> <b>{e.event_type}</b> {e.to_state && <>{e.from_state ?? "∅"} → <Badge s={e.to_state} /></>} <span className="mut">· {e.actor}</span></div>)}</div></div></>}</>);
}

function Decisions({ role, sel, setSel }: { role: Role; sel: number | null; setSel: (n: number) => void }) {
  const { picker, req, loading, error, reloadAll, empty } = useRequest(role, sel, setSel); const [choice, setChoice] = useState<Record<string, string>>({}); const [msg, setMsg] = useState<{ e?: string; ok?: string }>({});
  if (empty) return <div className="card">No requests yet.</div>;
  const f = req?.fulfillment;
  return (<><div className="card">{picker}<Status loading={loading} error={error} /><Status error={msg.e} ok={msg.ok} /></div>
    {req && !f && <div className="card">No inventory analysis yet (state: <Badge s={req.state} />).</div>}
    {f && <div className="card"><div className="row"><h2 style={{ margin: 0 }}>Fulfillment analysis</h2><Badge s={req.state} /></div><p className="mut">{f.explanation} Requested-quantity subtotal: {money(f.requested_subtotal)}</p>
      <table><thead><tr><th>SKU</th><th className="n">Requested</th><th className="n">Available</th><th className="n">Shortage</th><th>Recommendation</th></tr></thead>
        <tbody>{f.lines.map((l: Any) => <tr key={l.sku}><td>{l.sku}</td><td className="n">{l.requested}</td><td className="n">{l.available}</td><td className="n">{l.shortage}</td><td>{l.recommendation}</td></tr>)}</tbody></table>
      {f.lines.filter((l: Any) => l.shortage > 0).map((l: Any) => <div key={l.sku} style={{ marginTop: 12 }}><h3>{l.sku}: {l.rationale}</h3>
        {l.options.map((o: Any) => { const dec = req.decisions?.[l.sku]; const pickable = req.state === "CUSTOMER_DECISION_REQUIRED" && (o.id === "MANUAL_REVIEW" || o.feasibility === "FEASIBLE");
          return <label key={o.id} className={"opt" + ((choice[l.sku] ?? dec) === o.id ? " sel" : "")} style={{ display: "block" }}>
            <div className="row"><input type="radio" style={{ width: "auto" }} disabled={!pickable} checked={(choice[l.sku] ?? dec) === o.id} onChange={() => setChoice({ ...choice, [l.sku]: o.id })} />
              <b>{o.id}</b><span className={o.feasibility}>{o.feasibility}</span>{o.score != null && <span className="mut">score {o.score}</span>}{o.requires_customer_acceptance && <span className="badge">needs customer acceptance</span>}</div>
            <div className="mut">{o.rationale}</div></label>; })}</div>)}
      {req.state === "CUSTOMER_DECISION_REQUIRED" && <button className="btn" style={{ marginTop: 10 }} disabled={f.lines.some((l: Any) => l.shortage > 0 && !choice[l.sku])}
        onClick={() => act(role, `/requests/${req.id}/decisions`, { decisions: choice }, reloadAll, setMsg, "Decision recorded; workflow resumed")}>Submit customer decision</button>}
      {req.state === "NEEDS_HUMAN_REVIEW" && <div className="msg">Awaiting human review. {role === "manager" ? <button className="btn sec" onClick={() => act(role, `/requests/${req.id}/review`, { action: "REOPEN_DECISION" }, reloadAll, setMsg, "Reopened")}>Reopen customer decision</button> : "Switch role to manager to resolve."}</div>}</div>}</>);
}

function Quotes({ role, sel, setSel }: { role: Role; sel: number | null; setSel: (n: number) => void }) {
  const { picker, req, loading, error, reloadAll, empty } = useRequest(role, sel, setSel); const [msg, setMsg] = useState<{ e?: string; ok?: string }>({});
  if (empty) return <div className="card">No requests yet.</div>;
  return (<><div className="card">{picker}<Status loading={loading} error={error} /><Status error={msg.e} ok={msg.ok} /></div>
    {req && req.quotes.length === 0 && <div className="card">No quote yet (state: <Badge s={req.state} />). Quotes are created only after required decisions are resolved.</div>}
    {req?.quotes.map((q: Any) => <div className="card" key={q.id}><div className="row"><h2 style={{ margin: 0 }}>{q.quote_number}</h2><Badge s={q.status} /></div>
      <table><thead><tr><th>SKU</th><th>Item</th><th className="n">Qty</th><th className="n">Unit</th><th className="n">Total</th></tr></thead>
        <tbody>{q.lines.map((l: Any) => <tr key={l.sku}><td>{l.sku}</td><td>{l.name}</td><td className="n">{l.quantity}</td><td className="n">{money(l.unit_price)}</td><td className="n">{money(l.line_total)}</td></tr>)}
          <tr><td colSpan={4} className="n">Subtotal</td><td className="n">{money(q.subtotal)}</td></tr><tr><td colSpan={4} className="n">Discount ({q.discount_pct}%)</td><td className="n">−{money(q.discount_amount)}</td></tr>
          <tr><td colSpan={4} className="n"><b>Total (before tax & delivery)</b></td><td className="n"><b>{money(q.total)}</b></td></tr></tbody></table>
      {q.approval ? <div className="msg" style={{ marginTop: 8 }}><b>Approval:</b> <Badge s={q.approval.status} /> · requires {q.approval.required_role} · {q.approval.reason}{q.approval.resolved_by && <> · by {q.approval.resolved_by}</>}
        {q.approval.status === "PENDING" && <div className="row" style={{ marginTop: 6 }}>
          <button className="btn" onClick={() => act(role, `/approvals/${q.approval.id}/resolve`, { decision: "APPROVE" }, reloadAll, setMsg, "Approved")}>Approve</button>
          <button className="btn bad" onClick={() => act(role, `/approvals/${q.approval.id}/resolve`, { decision: "REJECT" }, reloadAll, setMsg, "Rejected")}>Reject</button>
          <span className="mut">Acting as <b>{role}</b>; the backend enforces the {q.approval.required_role} role.</span></div>}</div> : <p className="mut">No approval required (discount within sales rep limit).</p>}
      {req.state === "APPROVED" && <button className="btn" style={{ marginTop: 8 }} onClick={() => act(role, `/requests/${req.id}/finalize`, undefined, reloadAll, setMsg, "Finalized: inventory revalidated and reserved")}>Finalize (revalidates inventory & prices)</button>}</div>)}
    {req && <div className="card"><h2>Audit history</h2><div className="tl">{req.audit.map((e: Any) => <div key={e.id}><span className="mut">{new Date(e.ts).toLocaleString()}</span> <b>{e.event_type}</b> <span className="mut">· {e.actor}</span><pre>{JSON.stringify(e.details).slice(0, 220)}</pre></div>)}</div></div>}</>);
}
