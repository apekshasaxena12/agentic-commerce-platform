import { useEffect, useMemo, useState } from "react";
// Shared topbar (.topbar/.pill-nav/.pill-nav-item/.site-title) — same bar
// as the shopper page, so both pages read as one product. Imported after
// MerchantDashboard.css so its topbar rules aren't shadowed by it.
import "./MerchantDashboard.css";
import "./App.css";
import MerchantLogin from "./MerchantLogin";

// Day 13: points at the deployed Front Door 2 Render service (mcp_server's
// --http mode, see Dockerfile) via a Vite env var (VITE_MERCHANT_API_BASE,
// set in Vercel's project settings); falls back to localhost so nothing
// changes for local dev.
const API_BASE = import.meta.env.VITE_MERCHANT_API_BASE || "http://localhost:8765/merchant";

function formatMoney(n) {
  return `₹${Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function StockManage() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [adjusting, setAdjusting] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/products`, { credentials: "include" })
      .then((r) => r.json())
      .then(setProducts)
      .catch(() => setError("Could not reach the merchant API — is mcp_server.server --http running on :8765?"))
      .finally(() => setLoading(false));
  }, []);

  async function adjust(product_id, delta) {
    setAdjusting(product_id);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/products/${product_id}/stock`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delta }),
      });
      if (!res.ok) throw new Error();
      const updated = await res.json();
      setProducts((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch {
      setError(`Product #${product_id}: stock update failed`);
    } finally {
      setAdjusting(null);
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Stock</h2>
      </div>
      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && products.length === 0 && <p className="muted">No products in your catalog.</p>}
      {products.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Product</th>
              <th>Category</th>
              <th>Price</th>
              <th>Stock</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td className="muted">{p.category.replace(/_/g, " ")}</td>
                <td className="amount">{formatMoney(p.price)}</td>
                <td className="amount">{p.stock}</td>
                <td className="actions">
                  <button
                    className="stock-btn"
                    disabled={adjusting === p.id || p.stock === 0}
                    onClick={() => adjust(p.id, -1)}
                    aria-label={`Decrease stock for ${p.name}`}
                  >
                    −
                  </button>
                  <button
                    className="stock-btn"
                    disabled={adjusting === p.id}
                    onClick={() => adjust(p.id, 1)}
                    aria-label={`Increase stock for ${p.name}`}
                  >
                    +
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

function PendingApprovals() {
  const [approvals, setApprovals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [resolving, setResolving] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/pending-approvals`, { credentials: "include" });
      if (!res.ok) throw new Error();
      setApprovals(await res.json());
    } catch {
      setError("Could not reach the merchant API — is mcp_server.server --http running on :8765?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function resolve(approvalId, approved) {
    setResolving(approvalId);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/resolve-approval/${approvalId}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved }),
      });
      if (!res.ok) throw new Error();
      // The order is now resolved (completed or failed) — it drops off this
      // pending list rather than needing its row updated in place.
      setApprovals((prev) => prev.filter((a) => a.id !== approvalId));
    } catch {
      setError(`Approval #${approvalId}: ${approved ? "approve" : "reject"} failed`);
    } finally {
      setResolving(null);
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Pending approvals</h2>
        <button className="refresh" onClick={refresh}>
          Refresh
        </button>
      </div>
      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && approvals.length === 0 && (
        <p className="muted">No AI agent purchases are awaiting your approval right now.</p>
      )}
      {approvals.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Order</th>
              <th>Agent</th>
              <th>Amount</th>
              <th>Requested</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {approvals.map((a) => (
              <tr key={a.id}>
                <td>#{a.order_id}</td>
                <td>
                  <span className={`agent-badge ${a.agent_type}`}>{a.agent_name}</span>
                </td>
                <td className="amount">{formatMoney(a.amount)}</td>
                <td className="muted small">{new Date(a.requested_at).toLocaleString()}</td>
                <td className="actions">
                  <button
                    className="approve-btn"
                    disabled={resolving === a.id}
                    onClick={() => resolve(a.id, true)}
                  >
                    Approve
                  </button>
                  <button
                    className="reject-btn"
                    disabled={resolving === a.id}
                    onClick={() => resolve(a.id, false)}
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
    fetch(`${API_BASE}/agents`, { credentials: "include" })
      .then((r) => r.json())
      .then(setAgents)
      .catch(() => {});
  }, []);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Agents</h2>
      </div>
      <p className="muted small">
        Platform-wide — every buyer agent, not scoped to your store. Shown here since agents aren't
        merchant-specific (the same agent can shop at any store); their orders on the tabs above are.
      </p>
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
                <div className={`budget-fill ${a.type}`} style={{ width: `${pct}%` }} />
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
      const res = await fetch(`${API_BASE}/audit-trail`, { credentials: "include" });
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
        <h2>Full audit trail — your orders</h2>
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
  const [tab, setTab] = useState("stock");

  // null = still checking the session, false = not logged in, object = the
  // logged-in merchant ({id, name, email}) from GET /me or POST /login.
  const [merchant, setMerchant] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/me`, { credentials: "include" })
      .then((res) => (res.ok ? res.json() : Promise.reject()))
      .then(setMerchant)
      .catch(() => setMerchant(false));
  }, []);

  async function handleLogout() {
    await fetch(`${API_BASE}/logout`, { method: "POST", credentials: "include" });
    setMerchant(false);
  }

  if (merchant === null) {
    return null; // brief session check, avoids a login-form flash for an already-logged-in merchant
  }

  if (!merchant) {
    return <MerchantLogin onLoggedIn={setMerchant} />;
  }

  return (
    <div className="merchant-app">
      <header className="topbar">
        <nav className="pill-nav" aria-label="Primary">
          <a className="pill-nav-item" href="/">
            Shop
          </a>
          <button type="button" className="pill-nav-item active">
            Merchant
          </button>
        </nav>
        <a className="site-title" href="/">
          Shopfront
        </a>
        <button type="button" className="logout-btn" onClick={handleLogout}>
          Log out — {merchant.name}
        </button>
      </header>

      <nav className="merchant-tabs">
        <button className={tab === "stock" ? "active" : ""} onClick={() => setTab("stock")}>
          Stock
        </button>
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
        {tab === "stock" && <StockManage />}
        {tab === "approvals" && <PendingApprovals />}
        {tab === "audit" && <AuditTrail />}
        {tab === "agents" && <AgentOverview />}
      </main>
    </div>
  );
}
