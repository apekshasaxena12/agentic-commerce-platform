import { cartTotal } from "./cart.js";

// Frontend-only cart view: items/quantities/remove/running total, plus
// "Proceed to payment" which Results.jsx turns into a queue of individual
// single-item pipeline runs (see proceedToPayment/advanceCartQueue there) —
// nothing here talks to the backend directly.
export function CartPanel({ cart, onClose, onUpdateQuantity, onRemove, onProceed, processing }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card cart-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header-row">
          <h2>Your cart</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close cart">
            ×
          </button>
        </div>

        {cart.length === 0 && <p className="muted">Your cart is empty.</p>}

        {cart.length > 0 && (
          <>
            <div className="cart-items">
              {cart.map((item) => (
                <div className="cart-item" key={item.id}>
                  <div className="cart-item-info">
                    <div className="cart-item-name">{item.name}</div>
                    <div className="muted small">₹{item.price} each</div>
                  </div>
                  <div className="cart-item-qty">
                    <button
                      type="button"
                      disabled={processing}
                      onClick={() => onUpdateQuantity(item.id, item.quantity - 1)}
                      aria-label={`Decrease quantity of ${item.name}`}
                    >
                      −
                    </button>
                    <span>{item.quantity}</span>
                    <button
                      type="button"
                      disabled={processing}
                      onClick={() => onUpdateQuantity(item.id, item.quantity + 1)}
                      aria-label={`Increase quantity of ${item.name}`}
                    >
                      +
                    </button>
                  </div>
                  <div className="cart-item-subtotal">₹{(item.price * item.quantity).toFixed(2)}</div>
                  <button
                    type="button"
                    className="cart-item-remove"
                    disabled={processing}
                    onClick={() => onRemove(item.id)}
                    aria-label={`Remove ${item.name} from cart`}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>

            <div className="cart-total-row">
              <span>Total</span>
              <span>₹{cartTotal(cart).toFixed(2)}</span>
            </div>

            <button type="button" className="proceed-btn" onClick={onProceed} disabled={processing}>
              Proceed to payment
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// Shown once every item in a checkout run has been processed (completed or
// failed) — see Results.jsx's advanceCartQueue, which fires this the
// instant the queue empties and clears the cart at the same time.
export function CheckoutSummary({ results, onClose }) {
  const completed = results.filter((r) => r.status === "completed").length;
  const failed = results.length - completed;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header-row">
          <h2>Checkout summary</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <p>
          {completed} completed, {failed} failed out of {results.length}.
        </p>
        <ul className="summary-list">
          {results.map((r, i) => (
            <li key={i} className={r.status === "completed" ? "summary-ok" : "summary-fail"}>
              {r.name} — {r.status}
              {r.reason ? `: ${r.reason}` : ""}
            </li>
          ))}
        </ul>
        <button type="button" className="proceed-btn" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
