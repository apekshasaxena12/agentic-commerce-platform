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

// Turns a failed response into a real, specific message (e.g. "401: not
// authenticated") instead of a hardcoded guess — used by every tab below so
// an auth failure, a 500, or the API being unreachable each read distinctly.
async function describeError(res) {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return `${res.status}: ${body.detail}`;
  } catch {
    // response body wasn't JSON — fall through to the generic message
  }
  return `Request failed (${res.status} ${res.statusText})`.trim();
}

function StockManage() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [adjusting, setAdjusting] = useState(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_BASE}/products`, { credentials: "include" });
        if (!res.ok) throw new Error(await describeError(res));
        setProducts(await res.json());
      } catch (err) {
        setError(err.message || "Could not reach the merchant API");
      } finally {
        setLoading(false);
      }
    }
    load();
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
      {!loading && !error && products.length === 0 && <p className="muted">No products in your catalog.</p>}
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
      if (!res.ok) throw new Error(await describeError(res));
      setApprovals(await res.json());
    } catch (err) {
      setError(err.message || "Could not reach the merchant API");
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
      {!loading && !error && approvals.length === 0 && (
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_BASE}/agents`, { credentials: "include" });
        if (!res.ok) throw new Error(await describeError(res));
        setAgents(await res.json());
      } catch (err) {
        setError(err.message || "Could not reach the merchant API");
      } finally {
        setLoading(false);
      }
    }
    load();
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
      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && agents.length === 0 && <p className="muted">No agents found.</p>}
      {agents.length > 0 && (
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
                <dl className="agent-stats">
                  <dt>Total orders</dt>
                  <dd>{a.total_orders}</dd>
                  <dt>Successful</dt>
                  <dd>
                    {a.successful_orders}
                    {a.success_rate != null && <span className="muted"> ({a.success_rate}%)</span>}
                  </dd>
                  <dt>Policy blocks</dt>
                  <dd>{a.policy_block_count}</dd>
                  <dt>Payment failures</dt>
                  <dd>{a.payment_failure_count}</dd>
                </dl>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// Day 15: labels for the six real, computed-not-invented counts
// GET /merchant/incident-summary returns (see db/audit.py's
// get_incident_summary for exactly how each one is derived).
const INCIDENT_STATS = [
  { key: "total_orders", label: "Total orders" },
  { key: "auto_approved", label: "Auto-approved (no pause)" },
  { key: "merchant_approvals", label: "Merchant approvals" },
  { key: "policy_blocks", label: "Policy blocks" },
  { key: "payment_failures", label: "Payment failures" },
  { key: "unhandled_crashes", label: "Unhandled crashes" },
];

function IncidentCenter() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/incident-summary`, { credentials: "include" });
      if (!res.ok) throw new Error(await describeError(res));
      setSummary(await res.json());
    } catch (err) {
      setError(err.message || "Could not reach the merchant API");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Incident Center — your orders</h2>
        <button className="refresh" onClick={refresh}>
          Refresh
        </button>
      </div>
      <p className="muted small">
        Real counts computed from your orders and their audit trail — nothing here is estimated.
      </p>
      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {summary && (
        <div className="incident-stats">
          {INCIDENT_STATS.map(({ key, label }) => (
            <div key={key} className={`incident-stat ${key === "unhandled_crashes" && summary[key] > 0 ? "crash-nonzero" : ""}`}>
              <div className="incident-stat-value">{summary[key]}</div>
              <div className="incident-stat-label">{label}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// Day 17: own tab ("AI Command Center"), grouped with Incident Center and
// AI Commerce Score — all three are computed-signal summaries, distinct
// from the day-to-day Stock/Approvals/Audit tabs. Renders whatever
// suggestion types GET /merchant/growth-suggestions actually returned —
// repeat_purchase is omitted server-side entirely when no product
// qualifies, per the task's "don't force a suggestion that isn't there";
// the other three always appear, showing a real 0 when that's the honest
// answer, since a real zero is still real information.
function GrowthSuggestions() {
  const [suggestions, setSuggestions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/growth-suggestions`, { credentials: "include" });
      if (!res.ok) throw new Error(await describeError(res));
      setSuggestions(await res.json());
    } catch (err) {
      setError(err.message || "Could not reach the merchant API");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Growth Suggestions</h2>
        <button className="refresh" onClick={refresh}>
          Refresh
        </button>
      </div>
      <p className="muted small">
        Real, computed opportunities from your own catalog and order data — no invented revenue numbers.
      </p>
      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {suggestions && suggestions.length === 0 && <p className="muted">No suggestions available yet.</p>}
      {suggestions &&
        suggestions.map((s) => (
          <div key={s.type} className="suggestion-card">
            <div className="suggestion-card-head">
              <span>{s.title}</span>
              <span className="suggestion-count">{s.count}</span>
            </div>
            <p className="muted small">{s.why_it_matters}</p>
            {s.count === 0 && <p className="muted small">None found.</p>}
            {s.type === "bundle_opportunity" && s.items.length > 0 && (
              <ul className="suggestion-list">
                {s.items.map((it, i) => (
                  <li key={i}>{it.text}</li>
                ))}
              </ul>
            )}
            {s.type === "cross_sell_gap" && s.items.length > 0 && (
              <ul className="suggestion-list">
                {s.items.map((it) => (
                  <li key={it.id}>
                    #{it.id} {it.name}
                  </li>
                ))}
              </ul>
            )}
            {s.type === "low_stock_high_velocity" && s.items.length > 0 && (
              <ul className="suggestion-list">
                {s.items.map((it) => (
                  <li key={it.id}>
                    #{it.id} {it.name} — {it.stock} in stock
                  </li>
                ))}
              </ul>
            )}
            {s.type === "repeat_purchase" && s.items.length > 0 && (
              <ul className="suggestion-list">
                {s.items.map((it) => (
                  <li key={it.id}>
                    #{it.id} {it.name} — repurchased by {it.repeat_agent_count} agent(s)
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
    </section>
  );
}

// Day 16: own tab, placed next to Incident Center — both are "system
// health" style summaries (as opposed to Stock/Approvals/Audit's
// day-to-day operational views), so grouping them keeps the nav
// legible rather than burying this atop an unrelated tab.
function AICommerceScore() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/ai-commerce-score`, { credentials: "include" });
      if (!res.ok) throw new Error(await describeError(res));
      setData(await res.json());
    } catch (err) {
      setError(err.message || "Could not reach the merchant API");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>AI Commerce Score</h2>
        <button className="refresh" onClick={refresh}>
          Refresh
        </button>
      </div>
      <p className="muted small">
        Computed from your own catalog, policy, and order data — every number below is a real count, not an estimate.
      </p>
      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {data && (
        <>
          <div className="score-overall">
            <div className="score-overall-value">{data.overall_score}</div>
            <div className="score-overall-label">
              out of 100 — unweighted mean of the {data.dimensions.length} dimensions below
            </div>
          </div>
          <div className="score-dimensions">
            {data.dimensions.map((d) => (
              <div key={d.key} className="score-dimension">
                <div className="score-dimension-head">
                  <span>{d.label}</span>
                  <span className="score-dimension-value">{d.score == null ? "—" : `${d.score}%`}</span>
                </div>
                <div className="muted small">{d.detail}</div>
              </div>
            ))}
          </div>
        </>
      )}
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

// Day 15: one row per exported audit_log_entry, columns matching the
// on-screen table (order, agent, step, time, output, reasoning) — takes
// the already filtered+sorted rows, so the export always matches what's
// currently on screen rather than the full unfiltered dataset.
function exportAuditTrailCsv(rows) {
  function csvField(value) {
    const s = value == null ? "" : String(value);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  const header = ["Order", "Agent", "Step", "Time", "Output", "Reasoning"];
  const lines = [header, ...rows.map((r) => [
    r.order_id,
    r.agent_name,
    r.step,
    new Date(r.timestamp).toLocaleString(),
    r.output_summary,
    r.reasoning_text,
  ])].map((line) => line.map(csvField).join(","));

  const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `audit-trail-${new Date().toISOString().replace(/[:.]/g, "-")}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function AuditTrail() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [orderFilter, setOrderFilter] = useState("all");
  const [agentFilter, setAgentFilter] = useState("all");
  const [stepFilter, setStepFilter] = useState("all");
  // Default: most-recent-first. "Order"/"Agent"/"Step" stay as they were;
  // rather than redefining what "Order" sorts by (confusing — it should
  // keep meaning order id), default to the existing "Time" option instead
  // and make that comparator descending.
  const [sortBy, setSortBy] = useState("timestamp");

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/audit-trail`, { credentials: "include" });
      if (!res.ok) throw new Error(await describeError(res));
      setRows(await res.json());
    } catch (err) {
      setError(err.message || "Could not reach the merchant API");
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
      out = [...out].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp) || b.id - a.id);
    }
    return out;
  }, [rows, orderFilter, agentFilter, stepFilter, sortBy]);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Full audit trail — your orders</h2>
        <div className="panel-header-actions">
          <button className="refresh" onClick={() => exportAuditTrailCsv(filtered)} disabled={filtered.length === 0}>
            Export CSV
          </button>
          <button className="refresh" onClick={refresh}>
            Refresh
          </button>
        </div>
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

      {error && <div className="banner-error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && filtered.length === 0 && <p className="muted">No audit entries match this filter.</p>}
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
        <button className={tab === "incidents" ? "active" : ""} onClick={() => setTab("incidents")}>
          Incident Center
        </button>
        <button className={tab === "score" ? "active" : ""} onClick={() => setTab("score")}>
          AI Commerce Score
        </button>
        <button className={tab === "growth" ? "active" : ""} onClick={() => setTab("growth")}>
          Growth Suggestions
        </button>
        <button className={tab === "agents" ? "active" : ""} onClick={() => setTab("agents")}>
          Agents
        </button>
      </nav>

      <main className="merchant-content">
        {tab === "stock" && <StockManage />}
        {tab === "approvals" && <PendingApprovals />}
        {tab === "audit" && <AuditTrail />}
        {tab === "incidents" && <IncidentCenter />}
        {tab === "score" && <AICommerceScore />}
        {tab === "growth" && <GrowthSuggestions />}
        {tab === "agents" && <AgentOverview />}
      </main>
    </div>
  );
}
