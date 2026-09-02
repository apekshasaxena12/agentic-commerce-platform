import { useEffect, useRef, useState } from "react";
import "./App.css";
import CategoryIcon, { CartIcon, SendIcon } from "./icons.jsx";
import { cartCount, loadCart, saveCart } from "./cart.js";
import ProductModal from "./ProductModal.jsx";
import { CartPanel, CheckoutSummary } from "./CartPanel.jsx";

// Day 13: points at the deployed Render backend in production via a Vite
// env var (VITE_WS_URL, set in Vercel's project settings); falls back to
// localhost so nothing changes for local dev.
const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8000/ws/chat";

// Set by the landing page (App.jsx) right before navigating here — either
// {kind:"message", text} for typed/chip/category queries, or
// {kind:"browse", label, filters} for the gender toggle's real structured
// filter. Read and cleared ONCE here, at module scope — not inside the
// mount effect below. React 18+ StrictMode double-invokes effects in
// development (mount -> cleanup -> mount again, to surface non-idempotent
// effects); a getItem+removeItem done inside the effect body ran the
// "consume" step twice, so the surviving second invocation always saw an
// already-cleared value and never sent the query. A module top-level
// statement runs exactly once per page load regardless of how many times
// React later replays the effect, so the value is stable across both
// invocations.
const PENDING_QUERY_KEY = "shopfront_pending_query";
const _pendingRaw = sessionStorage.getItem(PENDING_QUERY_KEY);
sessionStorage.removeItem(PENDING_QUERY_KEY);
// let, not const: cleared after its one replay in the "connected" case below
// so a later reconnect (see connect()'s ws.onclose) doesn't re-send the
// original landing-page query a second time.
let pendingPayloadAtLoad = _pendingRaw ? JSON.parse(_pendingRaw) : null;

function ProductCard({ product, onBuy, onOpenDetail, disabled }) {
  const [imageError, setImageError] = useState(false);
  // Synchronous, immediate guard — not the `disabled` prop, which only
  // takes effect after React commits the parent's re-render. A rapid
  // double-click can fire the DOM click event twice before that commit
  // lands; a plain ref mutation blocks the second one the instant it
  // happens, same fix shape as the Day-9 confirm-button race. Buy now adds
  // to the cart now instead of triggering checkout, so the card no longer
  // unmounts on click — the guard clears itself on a short timeout instead
  // of relying on unmount to reset it, so a second legitimate click (e.g.
  // adding another unit) still works.
  const clickedRef = useRef(false);

  function handleBuyClick(e) {
    e.stopPropagation();
    if (clickedRef.current) return;
    clickedRef.current = true;
    onBuy(product);
    setTimeout(() => {
      clickedRef.current = false;
    }, 400);
  }

  return (
    <div
      className="product-card"
      onClick={() => onOpenDetail(product)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onOpenDetail(product)}
    >
      <div className="product-image-tile">
        {product.image_url && !imageError ? (
          <img
            src={product.image_url}
            alt={product.name}
            className="product-image"
            loading="lazy"
            onError={() => setImageError(true)}
          />
        ) : (
          <div className="product-icon-tile">
            <CategoryIcon category={product.category} className="product-icon" />
          </div>
        )}
      </div>
      <div className="product-name">{product.name}</div>
      <div className="product-category">{product.category.replace(/_/g, " ")}</div>
      {product.merchant_name && <div className="product-merchant">{product.merchant_name}</div>}
      <div className="product-price">₹{product.price}</div>
      <button className="buy-now" onClick={handleBuyClick} disabled={disabled}>
        Buy now
      </button>
    </div>
  );
}

// The signature element: every pipeline run is a sequence of checkpoints
// (intent -> retrieve -> recommend -> policy_check -> authorization ->
// razorpay -> verification) that a human buyer and an AI agent both pass
// through identically — rendered as a punched control card, not a plain log.
function AuditPanel({ entries }) {
  // The "recommend" step is still logged to audit_log_entry in full
  // (unfiltered, visible in the merchant dashboard's audit trail) and
  // renders in the chat log itself as its own "Also worth considering"
  // callout (see the "recommendation" WS message case above) — filtered
  // out of this checkpoint trail specifically so it isn't shown twice.
  const visibleEntries = entries.filter((e) => e.step !== "recommend");
  return (
    <div className="audit-panel">
      <h3>Live checkpoint trail</h3>
      {visibleEntries.length === 0 && <p className="audit-empty">Nothing yet — send a message.</p>}
      {visibleEntries.length > 0 && (
        <div className="course">
          {visibleEntries.map((e) => (
            <div key={e.id} className="checkpoint">
              <div className="audit-step">{e.step}</div>
              <div className="audit-output">{e.output_summary}</div>
              <div className="audit-reason">{e.reasoning_text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Results() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [awaitingConfirm, setAwaitingConfirm] = useState(null);
  const [auditEntries, setAuditEntries] = useState([]);
  const [busy, setBusy] = useState(false);
  const [checkoutPending, setCheckoutPending] = useState(false);
  const [connectionLost, setConnectionLost] = useState(false);
  const wsRef = useRef(null);
  const checkoutInfoRef = useRef(null);
  // "recommendation" persists in the LangGraph checkpoint state once set,
  // so the backend re-sends the same one on every subsequent turn of the
  // same checkout (confirm, then again after the webhook) — this dedupes
  // so the callout renders once per checkout instead of three times.
  const lastRecommendationRef = useRef(null);
  // Distinguishes an intentional close (component unmount, e.g. navigating
  // away) from an unexpected one (network drop, a proxy's idle-connection
  // timeout during a slow pipeline run, or the server itself losing the
  // socket) — onclose fires either way, but only the latter should show the
  // "connection lost" banner and trigger a reconnect.
  const isUnmountingRef = useRef(false);
  // Read inside ws.onclose, which — like ws.onmessage — is wired up once at
  // mount and would otherwise close over stale state (same reasoning as
  // cartProcessingRef above).
  const pendingRequestRef = useRef(false);
  useEffect(() => {
    pendingRequestRef.current = busy || checkoutPending || !!awaitingConfirm;
  }, [busy, checkoutPending, awaitingConfirm]);
  // At most one automatic reconnect — if that one also fails, the banner's
  // manual "Reconnect" button is still there, rather than silently retrying
  // forever.
  const autoReconnectedRef = useRef(false);

  // --- Cart + product modal state (Part 2/3/4) ---
  const [cart, setCart] = useState(() => loadCart());
  const [cartOpen, setCartOpen] = useState(false);
  const [modalProductId, setModalProductId] = useState(null);
  // True from "Proceed to payment" until the single combined order this
  // produces (see proceedToPayment) reaches a final state — the whole
  // cart bills as one order/one payment, not item-by-item, so there's no
  // queue to advance, just one confirm step and one Checkout.js run.
  const [cartProcessing, setCartProcessing] = useState(false);
  const [checkoutSummary, setCheckoutSummary] = useState(null);
  // Ref mirror of cartProcessing, and a snapshot of the cart's items at
  // the moment checkout started — both read inside ws.onmessage's
  // chat-message/summary-building logic, where state itself is stale
  // (that handler is set up once at mount; see its effect below).
  const cartProcessingRef = useRef(false);
  const cartSnapshotRef = useRef([]);

  // Extracted so the automatic/manual reconnect below can call it again with
  // the exact same wiring — a fresh WebSocket always gets a fresh thread_id
  // from the server (see server/app.py's ws_chat), so this is a new chat
  // session, not a resume of whatever was in flight on the old one.
  function connect() {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case "connected": {
          setConnectionLost(false);
          const pending = pendingPayloadAtLoad;
          pendingPayloadAtLoad = null;
          if (pending?.kind === "browse") {
            sendBrowse(pending.label, pending.filters);
          } else if (pending?.kind === "message") {
            sendChatMessage(pending.text);
          }
          break;
        }
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
          // The recommend node's real cross-sell reason (grounded in
          // co_purchase_stat, drafted by Groq — see pipeline/graph.py's
          // _recommend_impl), surfaced as its own callout rather than a
          // plain system line so it reads as a suggestion, not a status
          // update.
          if (lastRecommendationRef.current !== msg.reason) {
            lastRecommendationRef.current = msg.reason;
            addMessage("recommendation", msg.reason);
          }
          break;
        case "awaiting_confirm":
          setAwaitingConfirm({ order_id: msg.order_id, amount: msg.amount });
          addMessage(
            "system",
            cartProcessingRef.current
              ? `Ready to confirm order #${msg.order_id} for ₹${msg.amount} (${cartSnapshotRef.current.length} item(s)).`
              : `Ready to confirm order #${msg.order_id} for ₹${msg.amount}.`
          );
          break;
        case "start_checkout":
          setAwaitingConfirm(null);
          setCheckoutPending(true);
          checkoutInfoRef.current = msg;
          addMessage("system", `Order created (razorpay_order_id=${msg.razorpay_order_id}). Opening Checkout...`);
          openRazorpayCheckout(msg);
          break;
        case "final_status":
          setCheckoutPending(false);
          addMessage("system", `Order status: ${msg.status}.`);
          // Combined cart checkout concludes in one shot — every cart
          // item shares this same order's outcome, so the summary marks
          // them all with it and the whole cart clears together.
          if (cartProcessingRef.current) {
            cartProcessingRef.current = false;
            setCartProcessing(false);
            setCheckoutSummary(cartSnapshotRef.current.map((i) => ({ name: i.name, status: msg.status })));
            setCart([]);
            saveCart([]);
          }
          break;
        case "order_failed":
          addMessage("system", `Order rejected: ${msg.reason}`);
          setAwaitingConfirm(null);
          setCheckoutPending(false);
          if (cartProcessingRef.current) {
            cartProcessingRef.current = false;
            setCartProcessing(false);
            setCheckoutSummary(
              cartSnapshotRef.current.map((i) => ({ name: i.name, status: "failed", reason: msg.reason }))
            );
            setCart([]);
            saveCart([]);
          }
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

    ws.onerror = () => {
      // Always followed by onclose (per the WebSocket spec) — the actual
      // user-facing handling (banner, clearing busy, reconnect) lives there
      // so it isn't duplicated; this is just for local debugging visibility.
      console.error("WebSocket error on", WS_URL);
    };

    ws.onclose = () => {
      if (isUnmountingRef.current) return; // a real navigation-away, not a drop

      setBusy(false);
      setCheckoutPending(false);
      setAwaitingConfirm(null);
      setConnectionLost(true);
      addMessage(
        "system",
        pendingRequestRef.current
          ? "Connection lost while your last request was still in progress. It may or may not have completed on the server — check your order/cart before retrying a purchase."
          : "Connection lost."
      );

      if (!autoReconnectedRef.current) {
        autoReconnectedRef.current = true;
        setTimeout(() => {
          if (!isUnmountingRef.current) connect();
        }, 1500);
      }
    };

    return ws;
  }

  useEffect(() => {
    isUnmountingRef.current = false;
    connect();
    return () => {
      // wsRef.current, not a captured local — an automatic reconnect above
      // may have replaced it with a newer socket by the time this runs.
      isUnmountingRef.current = true;
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Navigating away mid-purchase (awaiting a confirm, or Checkout.js is
  // open / a payment webhook is pending) doesn't corrupt anything server
  // side — the pipeline's Postgres checkpointer keeps the paused order
  // intact — but there's no UI to resume a stranded order from a fresh
  // page load, so it'd otherwise be silently abandoned. A native
  // "are you sure you want to leave" prompt is the cheapest honest fix.
  useEffect(() => {
    if (!awaitingConfirm && !checkoutPending) return;
    function handleBeforeUnload(e) {
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [awaitingConfirm, checkoutPending]);

  function addMessage(role, text) {
    setMessages((prev) => [...prev, { role, text, id: prev.length }]);
  }

  function send(payload) {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }

  // Single path for every chat-shaped WS message, whether it came from the
  // text input or a "Buy now" click on a product card — same message
  // shape, same pipeline entry point either way.
  function sendChatMessage(text) {
    if (!text.trim() || busy) return;
    addMessage("user", text);
    setSearchResults([]);
    setBusy(true);
    send({ type: "message", text });
  }

  function sendMessage() {
    sendChatMessage(input);
    setInput("");
  }

  // The gender toggle's path: a real structured filter sent straight to
  // search_products (see server/app.py's "browse" handler), bypassing
  // intent classification entirely rather than faking a text query.
  function sendBrowse(label, filters) {
    if (busy) return;
    addMessage("user", `Browsing: ${label}`);
    setSearchResults([]);
    setBusy(true);
    send({ type: "browse", filters });
  }

  // Part 3: Buy now (card or modal) queues into the cart — it does NOT
  // trigger the pipeline. Checkout only happens from "Proceed to payment"
  // (proceedToPayment below).
  function addToCart(product) {
    setCart((prev) => {
      const idx = prev.findIndex((i) => i.id === product.id);
      const next =
        idx >= 0
          ? prev.map((i, k) => (k === idx ? { ...i, quantity: i.quantity + 1 } : i))
          : [
              ...prev,
              {
                id: product.id,
                name: product.name,
                price: product.price,
                image_url: product.image_url,
                category: product.category,
                quantity: 1,
              },
            ];
      saveCart(next);
      return next;
    });
  }

  function updateCartQuantity(id, quantity) {
    setCart((prev) => {
      const next =
        quantity <= 0 ? prev.filter((i) => i.id !== id) : prev.map((i) => (i.id === id ? { ...i, quantity } : i));
      saveCart(next);
      return next;
    });
  }

  function removeFromCart(id) {
    updateCartQuantity(id, 0);
  }

  function buyNow(product) {
    addToCart(product);
    addMessage("system", `Added ${product.name} to cart.`);
  }

  function openProductModal(product) {
    setCartOpen(false);
    setModalProductId(product.id);
  }

  // --- Part 4: checkout the cart as ONE combined order — a single
  // "checkout_cart" message carrying every {product_id, quantity} pair
  // (see server/app.py's _handle_checkout_cart and pipeline/graph.py's
  // cart_items branch in _intent_impl/_retrieve_impl), which creates one
  // order covering the whole cart and runs recommend/policy_check/
  // authorization/razorpay/verification exactly once for it — one confirm
  // step, one Checkout.js flow, one audit trail, unmodified downstream of
  // where the order gets resolved. Deliberately traded away per-item
  // failure isolation for a simpler single-payment checkout: if this one
  // payment fails, the whole cart fails together (see the final_status/
  // order_failed cases above, which mark every cart item with the same
  // outcome). cartSnapshotRef/cartProcessingRef exist because this
  // function's follow-through lives inside ws.onmessage, which closed
  // over this component's very first render — see that handler's
  // mount-time effect above.
  function proceedToPayment() {
    if (cart.length === 0 || busy || cartProcessing) return;
    const items = cart.map((item) => ({ product_id: item.id, quantity: item.quantity }));
    cartSnapshotRef.current = cart.map((item) => ({ name: item.name }));
    cartProcessingRef.current = true;
    setCheckoutSummary(null);
    setCartOpen(false);
    setCartProcessing(true);
    setSearchResults([]);
    setBusy(true);
    send({ type: "checkout_cart", items });
  }

  function handleManualReconnect() {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    connect();
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
      // There's no login/auth system (see server/app.py's scope note — every
      // chat connection is hardcoded to HUMAN_AGENT_ID), so the shopper's
      // contact details are already known; prefilling them here just skips
      // Checkout.js's redundant "enter your number" screen instead of
      // asking a known demo user again.
      prefill: { name: "Demo Shopper", email: "demo.shopper@example.com", contact: "9876543210" },
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
      theme: { color: "#ff5a36" },
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
      <header className="topbar">
        <nav className="pill-nav" aria-label="Primary">
          <button type="button" className="pill-nav-item active">
            Shop
          </button>
          <a className="pill-nav-item" href="/merchant">
            Merchant
          </a>
        </nav>
        <a className="site-title" href="/">
          Shopfront
        </a>
        <button
          type="button"
          className="cart-btn"
          onClick={() => {
            setModalProductId(null);
            setCartOpen(true);
          }}
          aria-label="Open cart"
        >
          <CartIcon />
          {cartCount(cart) > 0 && <span className="cart-badge">{cartCount(cart)}</span>}
        </button>
      </header>

      <div className="app-body results-body">
        <div className="main-column">
          {connectionLost && (
            <div className="connection-banner">
              <span>Connection lost — reconnecting automatically. If this doesn't clear, use the button.</span>
              <button type="button" onClick={handleManualReconnect}>
                Reconnect
              </button>
            </div>
          )}

          {cartProcessing && (
            <div className="cart-progress">
              Processing your order — {cart.length} item{cart.length !== 1 ? "s" : ""}: {cart.map((i) => i.name).join(", ")}…
            </div>
          )}

          <div className="chat-log">
            {messages.map((m) => (
              <div key={m.id} className={`chat-bubble ${m.role}`}>
                {m.role === "recommendation" && <span className="recommendation-label">Also worth considering</span>}
                {m.text}
              </div>
            ))}
          </div>

          {searchResults.length > 0 && (
            <div className="product-grid">
              {searchResults.map((p) => (
                <ProductCard key={p.id} product={p} onBuy={buyNow} onOpenDetail={openProductModal} disabled={busy} />
              ))}
            </div>
          )}

          {awaitingConfirm && (
            <div className="confirm-box">
              <div className="confirm-text">
                <span className="confirm-mark">● cleared by policy engine</span>
                <span className="confirm-amount">Confirm purchase — ₹{awaitingConfirm.amount}</span>
                {cartProcessing && (
                  <span className="confirm-cart-context">
                    {cart.length} item{cart.length !== 1 ? "s" : ""}: {cart.map((i) => i.name).join(", ")}
                  </span>
                )}
              </div>
              <div className="confirm-actions">
                <button onClick={() => sendConfirm("confirm")}>Confirm purchase</button>
                <button className="secondary" onClick={() => sendConfirm("reject")}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          <div className="results-composer">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage()}
              placeholder="Continue refining, or ask to buy something else…"
              disabled={busy}
              aria-label="Continue the conversation"
            />
            <button type="button" className="chatbox-send" onClick={sendMessage} disabled={busy} aria-label="Send">
              <SendIcon />
            </button>
          </div>
        </div>

        <AuditPanel entries={auditEntries} />
      </div>

      {modalProductId != null && (
        <ProductModal
          productId={modalProductId}
          onClose={() => setModalProductId(null)}
          onAddToCart={(product) => {
            addToCart(product);
            addMessage("system", `Added ${product.name} to cart.`);
            setModalProductId(null);
          }}
          onAddMiniToCart={(product) => {
            addToCart(product);
            addMessage("system", `Added ${product.name} to cart.`);
          }}
        />
      )}

      {cartOpen && (
        <CartPanel
          cart={cart}
          onClose={() => setCartOpen(false)}
          onUpdateQuantity={updateCartQuantity}
          onRemove={removeFromCart}
          onProceed={proceedToPayment}
          processing={cartProcessing}
        />
      )}

      {checkoutSummary && <CheckoutSummary results={checkoutSummary} onClose={() => setCheckoutSummary(null)} />}
    </div>
  );
}
