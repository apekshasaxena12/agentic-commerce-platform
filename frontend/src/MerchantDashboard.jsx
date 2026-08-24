import { useEffect, useMemo, useRef, useState } from "react";
import "./MerchantDashboard.css";

// Day 13: points at the deployed Front Door 2 Render service (mcp_server's
// --http mode, see Dockerfile) via Vite env vars (VITE_MERCHANT_API_BASE/
// VITE_MERCHANT_WS_URL, set in Vercel's project settings); falls back to
// localhost so nothing changes for local dev.
const API_BASE = import.meta.env.VITE_MERCHANT_API_BASE || "http://localhost:8765/merchant";
const WS_URL = import.meta.env.VITE_MERCHANT_WS_URL || "ws://localhost:8765/merchant/ws";

function formatMoney(n) {
  return `₹${Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function productLabel(items) {
  if (!items || items.length === 0) return "—";
  const first = items[0];
  return items.length > 1
    ? `${first.name} +${items.length - 1} more`
    : `${first.name} (x${first.quantity})`;
}

function PendingApprovals({ connected, liveEvent }) {
  const [approvals, setApprovals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState(null);
  const [error, setError] = useState(null);

  async function refresh() {
    try {
      const res = await fetch(`${API_BASE}/pending-approvals`);
      const data = await res.json();
      setApprovals(data);
    } catch {
      setError("Could not reach the merchant API — is mcp_server.server --http running on :8765?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-fetch the snapshot whenever the shared dashboard socket reports a new
  // or resolved approval — see MerchantDashboard's single WebSocket below.
  useEffect(() => {
    if (liveEvent && (liveEvent.type === "pending_approval_created" || liveEvent.type === "approval_resolved")) {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveEvent]);

  async function resolve(order_id, approved) {
    setActioning(order_id);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/resolve-approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order_id, approved, resolved_by: "merchant_dashboard" }),
      });
      const result = await res.json();
      if (result.outcome === "error") {
        setError(`Order #${order_id}: ${result.reason}`);
      }
      await refresh();
    } catch {
      setError(`Order #${order_id}: request failed`);
    } finally {
      setActioning(null);
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Pending approvals</h2>
        <span className={`live-dot ${connected ? "live" : "down"}`}>
          {connected ? "live" : "disconnected"}
        </span>
      </div>
      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && approvals.length === 0 && (
        <p className="muted">No orders currently awaiting approval.</p>
      )}
      {approvals.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Order</th>
              <th>Requested by</th>
              <th>Product</th>
              <th>Amount</th>
              <th>Threshold</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {approvals.map((a) => (
              <tr key={a.approval_request_id}>
                <td>#{a.order_id}</td>
                <td>
                  <span className={`agent-badge ${a.agent_type}`}>{a.agent_name}</span>
                </td>
                <td>{productLabel(a.items)}</td>
                <td className="amount over">{formatMoney(a.amount)}</td>
                <td className="muted">{formatMoney(a.threshold)}</td>
                <td className="actions">
                  <button
                    className="approve"
                    disabled={actioning === a.order_id}
                    onClick={() => resolve(a.order_id, true)}
                  >
                    Approve
                  </button>
                  <button
                    className="reject"
                    disabled={actioning === a.order_id}
                    onClick={() => resolve(a.order_id, false)}
                  >
                    Reject
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function AgentOverview() {
  const [agents, setAgents] = useState([]);

  useEffect(() => {
    fetch(`${API_BASE}/agents`)
      .then((r) => r.json())
      .then(setAgents)
      .catch(() => {});
  }, []);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Agents</h2>
      </div>
      <div className="agent-cards">
        {agents.map((a) => {
          const pct = a.budget_limit > 0 ? Math.min(100, (a.spent_so_far / a.budget_limit) * 100) : 0;
          return (
            <div key={a.id} className="agent-card">
              <div className="agent-card-top">
                <span className={`agent-badge ${a.type}`}>{a.type === "ai_agent" ? "AI agent" : "Human"}</span>
                <span className="agent-name">{a.name}</span>
              </div>
              <div className="budget-bar">
                <div className="budget-fill" style={{ width: `${pct}%` }} />
              </div>
              <div className="budget-numbers">
                <span>{formatMoney(a.spent_so_far)} spent</span>
                <span className="muted">of {formatMoney(a.budget_limit)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const STEP_ORDER = [
  "intent",
  "retrieve",
  "recommend",
  "policy_check",
  "authorization",
  "razorpay",
  "verification",
];

function AuditTrail() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [orderFilter, setOrderFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [stepFilter, setStepFilter] = useState("all");
  const [sortBy, setSortBy] = useState("order_id");

  async function refresh() {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/audit-trail`);
      setRows(await res.json());
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const orders = useMemo(
    () =>
      [...new Map(rows.map((r) => [r.order_id, { id: r.order_id, status: r.order_status, agent: r.agent_name }])).values()].sort(
        (a, b) => a.id - b.id
      ),
    [rows]
  );
  const agents = useMemo(() => [...new Set(rows.map((r) => r.agent_name))].sort(), [rows]);
  const steps = useMemo(
    () => STEP_ORDER.filter((s) => rows.some((r) => r.step === s)),
    [rows]
  );

  const filtered = useMemo(() => {
    let out = rows.filter(
      (r) =>
        (orderFilter === "all" || String(r.order_id) === orderFilter) &&
        (agentFilter === "all" || r.agent_name === agentFilter) &&
        (stepFilter === "all" || r.step === stepFilter)
    );
    if (sortBy === "order_id") {
      out = [...out].sort((a, b) => a.order_id - b.order_id || a.id - b.id);
    } else if (sortBy === "agent") {
      out = [...out].sort((a, b) => a.agent_name.localeCompare(b.agent_name) || a.id - b.id);
    } else if (sortBy === "step") {
      out = [...out].sort(
        (a, b) => STEP_ORDER.indexOf(a.step) - STEP_ORDER.indexOf(b.step) || a.order_id - b.order_id
      );
    } else if (sortBy === "timestamp") {
      out = [...out].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    }
    return out;
  }, [rows, orderFilter, agentFilter, stepFilter, sortBy]);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Full audit trail — all orders</h2>
        <button className="refresh" onClick={refresh}>
          Refresh
        </button>
      </div>

      <div className="filter-row">
        <label>
          Order
          <select value={orderFilter} onChange={(e) => setOrderFilter(e.target.value)}>
            <option value="all">All orders</option>
            {orders.map((o) => (
              <option key={o.id} value={o.id}>
                #{o.id} — {o.status} ({o.agent})
              </option>
            ))}
          </select>
        </label>
        <label>
          Agent
          <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
            <option value="all">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <label>
          Step
          <select value={stepFilter} onChange={(e) => setStepFilter(e.target.value)}>
            <option value="all">All steps</option>
            {steps.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Sort by
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            <option value="order_id">Order</option>
            <option value="agent">Agent</option>
            <option value="step">Pipeline step</option>
            <option value="timestamp">Time</option>
          </select>
        </label>
      </div>

      {loading && <p className="muted">Loading…</p>}
      {!loading && filtered.length === 0 && <p className="muted">No audit entries match this filter.</p>}
      {filtered.length > 0 && (
        <div className="audit-trail-scroll">
          <table className="data-table audit-table">
            <thead>
              <tr>
                <th>Order</th>
                <th>Agent</th>
                <th>Step</th>
                <th>Time</th>
                <th>Output</th>
                <th>Reasoning</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id} className={r.order_status === "failed" ? "row-failed" : ""}>
                  <td>
                    #{r.order_id}
                    <div className="muted small">{r.order_status}</div>
                  </td>
                  <td>
                    <span className={`agent-badge ${r.agent_type}`}>{r.agent_name}</span>
                  </td>
                  <td>
                    <span className="step-chip">{r.step}</span>
                  </td>
                  <td className="muted small">{new Date(r.timestamp).toLocaleString()}</td>
                  <td>{r.output_summary}</td>
                  <td className="muted">{r.reasoning_text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function MerchantDashboard() {
  const [tab, setTab] = useState("approvals");
  const [wsConnected, setWsConnected] = useState(false);
  const [liveEvent, setLiveEvent] = useState(null);
  const wsRef = useRef(null);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;
    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onmessage = (event) => setLiveEvent(JSON.parse(event.data));
    return () => ws.close();
  }, []);

  return (
    <div className="merchant-app">
      <header className="merchant-topbar">
        <div className="merchant-topbar-left">
          <span className="merchant-logo">MERCHANT CONSOLE</span>
          <span className="merchant-subtitle">Agentic Commerce — bounded, explainable, gated</span>
        </div>
        <a className="back-link" href="/">
          ← shopper view
        </a>
      </header>

      <nav className="merchant-tabs">
        <button className={tab === "approvals" ? "active" : ""} onClick={() => setTab("approvals")}>
          Pending approvals
        </button>
        <button className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")}>
          Audit trail
        </button>
        <button className={tab === "agents" ? "active" : ""} onClick={() => setTab("agents")}>
          Agents
        </button>
      </nav>

      <main className="merchant-content">
        {tab === "approvals" && <PendingApprovals connected={wsConnected} liveEvent={liveEvent} />}
        {tab === "audit" && <AuditTrail />}
        {tab === "agents" && <AgentOverview />}
      </main>
    </div>
  );
}
