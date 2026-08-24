import { useEffect, useRef, useState } from "react";
import "./App.css";

// Day 13: points at the deployed Render backend in production via a Vite
// env var (VITE_WS_URL, set in Vercel's project settings); falls back to
// localhost so nothing changes for local dev.
const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/chat";

function ProductCard({ product }) {
  return (
    <div className="product-card">
      <div className="product-image-placeholder">{product.category.slice(0, 1).toUpperCase()}</div>
      <div className="product-name">{product.name}</div>
      <div className="product-category">{product.category}</div>
      <div className="product-price">₹{product.price}</div>
    </div>
  );
}

function AuditPanel({ entries }) {
  return (
    <div className="audit-panel">
      <h3>Live audit trail</h3>
      {entries.length === 0 && <p className="audit-empty">Nothing yet — send a message.</p>}
      {entries.map((e) => (
        <div key={e.id} className="audit-row">
          <div className="audit-step">{e.step}</div>
          <div className="audit-output">{e.output_summary}</div>
          <div className="audit-reason">{e.reasoning_text}</div>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [recommendation, setRecommendation] = useState(null);
  const [awaitingConfirm, setAwaitingConfirm] = useState(null);
  const [auditEntries, setAuditEntries] = useState([]);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const wsRef = useRef(null);
  const checkoutInfoRef = useRef(null);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case "connected":
          break;
        case "audit_entry":
          setAuditEntries((prev) => [...prev, msg]);
          break;
        case "search_results":
          setSearchResults(msg.results);
          if (msg.results.length > 0) {
            addMessage("system", `Found ${msg.results.length} matching product(s).`);
          } else {
            addMessage("system", "No matching products found.");
          }
          break;
        case "recommendation":
          setRecommendation(msg);
          break;
        case "awaiting_confirm":
          setAwaitingConfirm({ order_id: msg.order_id, amount: msg.amount });
          addMessage("system", `Ready to confirm order #${msg.order_id} for ₹${msg.amount}.`);
          break;
        case "start_checkout":
          setAwaitingConfirm(null);
          checkoutInfoRef.current = msg;
          addMessage("system", `Order created (razorpay_order_id=${msg.razorpay_order_id}). Opening Checkout...`);
          openRazorpayCheckout(msg);
          break;
        case "final_status":
          addMessage("system", `Order status: ${msg.status}.`);
          break;
        case "order_failed":
          addMessage("system", `Order rejected: ${msg.reason}`);
          setAwaitingConfirm(null);
          break;
        case "paused":
          addMessage("system", `Pipeline paused: ${JSON.stringify(msg.detail)}`);
          break;
        case "turn_complete":
          setBusy(false);
          break;
        case "error":
          addMessage("system", `Error: ${msg.message}`);
          setBusy(false);
          break;
        default:
          break;
      }
    };

    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function addMessage(role, text) {
    setMessages((prev) => [...prev, { role, text, id: prev.length }]);
  }

  function send(payload) {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }

  function sendMessage() {
    if (!input.trim() || busy) return;
    addMessage("user", input);
    setSearchResults([]);
    setRecommendation(null);
    setBusy(true);
    send({ type: "message", text: input });
    setInput("");
  }

  function sendConfirm(decision) {
    // Hide the confirm box immediately (optimistic), not just after the
    // server round-trip — otherwise a fast double-click can fire "confirm"
    // twice before the button disappears. The backend also now guards
    // against a duplicate/stale confirm being misapplied, but this fixes
    // the actual trigger.
    setAwaitingConfirm(null);
    setBusy(true);
    send({ type: "confirm", decision });
  }

  function openRazorpayCheckout({ order_id, razorpay_order_id, amount, key_id }) {
    if (!window.Razorpay) {
      addMessage("system", "Razorpay Checkout.js did not load — check your network/CDN access.");
      return;
    }
    const amountPaise = Math.round(amount * 100);

    const options = {
      key: key_id,
      amount: amountPaise,
      currency: "INR",
      name: "Agentic Commerce Demo",
      description: `Order #${order_id}`,
      order_id: razorpay_order_id,
      handler: function (response) {
        addMessage("system", `Checkout succeeded: payment_id=${response.razorpay_payment_id}`);
        send({
          type: "checkout_outcome",
          status: "captured",
          razorpay_payment_id: response.razorpay_payment_id,
          razorpay_order_id: response.razorpay_order_id,
          amount_paise: amountPaise,
        });
      },
      modal: {
        ondismiss: function () {
          addMessage("system", "Checkout closed without completing payment.");
        },
      },
      theme: { color: "#3399cc" },
    };

    const rzp = new window.Razorpay(options);
    rzp.on("payment.failed", function (response) {
      const err = response.error || {};
      addMessage(
        "system",
        `Checkout REAL decline: code=${err.code} reason=${err.reason} description=${err.description}`
      );
      send({
        type: "checkout_outcome",
        status: "failed",
        razorpay_payment_id: err.metadata && err.metadata.payment_id,
        razorpay_order_id: (err.metadata && err.metadata.order_id) || razorpay_order_id,
        amount_paise: amountPaise,
        error: {
          code: err.code,
          description: err.description,
          source: err.source,
          step: err.step,
          reason: err.reason,
        },
      });
    });
    rzp.open();
  }

  return (
    <div className="app">
      <div className="main-column">
        <h1>Agentic Commerce — Chat</h1>
        <div className="connection-status">{connected ? "connected" : "disconnected"}</div>

        <div className="chat-log">
          {messages.map((m) => (
            <div key={m.id} className={`chat-bubble ${m.role}`}>
              {m.text}
            </div>
          ))}
        </div>

        {searchResults.length > 0 && (
          <div className="product-grid">
            {searchResults.map((p) => (
              <ProductCard key={p.id} product={p} />
            ))}
          </div>
        )}

        {recommendation && (
          <div className="recommendation">
            <strong>Also consider: {recommendation.name}</strong>
            <div className="recommendation-reason">{recommendation.reason}</div>
          </div>
        )}

        {awaitingConfirm && (
          <div className="confirm-box">
            <div>Confirm purchase — ₹{awaitingConfirm.amount}</div>
            <button onClick={() => sendConfirm("confirm")}>Confirm purchase</button>
            <button className="secondary" onClick={() => sendConfirm("reject")}>
              Cancel
            </button>
          </div>
        )}

        <div className="input-row">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && sendMessage()}
            placeholder="e.g. Buy the DryTech Running Tee"
            disabled={busy}
          />
          <button onClick={sendMessage} disabled={busy}>
            Send
          </button>
        </div>
      </div>

      <AuditPanel entries={auditEntries} />
    </div>
  );
}
