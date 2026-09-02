import { useEffect, useState } from "react";
import CategoryIcon from "./icons.jsx";

// Same origin convention as Results.jsx's WS_URL (VITE_ vars, localhost
// fallback for local dev).
const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

// Shared by both "Similar items" and "You might also like" — each entry
// there is a full product (search results already carry image_url; the
// cross_sell rows now select it too, see catalog/retrieval.py) so it gets
// the same picture + Buy now affordance as a main product card.
function MiniProductCard({ product, subtext, onAddToCart }) {
  const [imageError, setImageError] = useState(false);
  return (
    <div className="modal-mini-card">
      <div className="modal-mini-image-tile">
        {product.image_url && !imageError ? (
          <img
            src={product.image_url}
            alt={product.name}
            className="modal-mini-image"
            loading="lazy"
            onError={() => setImageError(true)}
          />
        ) : (
          <CategoryIcon category={product.category} className="modal-mini-icon" />
        )}
      </div>
      <div className="modal-mini-name">{product.name}</div>
      <div className="modal-mini-price">{subtext}</div>
      <button className="buy-now modal-mini-buy" onClick={() => onAddToCart(product)}>
        Buy now
      </button>
    </div>
  );
}

// Product detail modal: opened by clicking a product card (not its Buy now
// button). Fetches server/app.py's GET /api/products/{id}, which itself is
// pure orchestration over two already-existing catalog lookups
// (get_product_detail, search_products) — no new retrieval logic here or
// on the backend.
export default function ProductModal({ productId, onClose, onAddToCart, onAddMiniToCart }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    fetch(`${API_BASE}/api/products/${productId}`)
      .then((res) => {
        if (!res.ok) throw new Error(`Product lookup failed (${res.status})`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [productId]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card product-modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>

        {error && <p className="modal-error">{error}</p>}
        {!error && !detail && <p className="muted">Loading…</p>}

        {detail && (
          <>
            <div className="modal-product-header">
              <div className="modal-product-image">
                {detail.image_url ? (
                  <img src={detail.image_url} alt={detail.name} />
                ) : (
                  <CategoryIcon category={detail.category} className="product-icon" />
                )}
              </div>
              <div className="modal-product-summary">
                <h2>{detail.name}</h2>
                <div className="product-category">{detail.category.replace(/_/g, " ")}</div>
                <div className="product-price modal-price">₹{detail.price}</div>
                <button className="buy-now" onClick={() => onAddToCart(detail)}>
                  Buy now
                </button>
              </div>
            </div>

            <p className="modal-description">{detail.semantic_description}</p>

            {Object.keys(detail.structured_attributes || {}).length > 0 && (
              <div className="modal-attrs">
                {Object.entries(detail.structured_attributes).map(([key, value]) => (
                  <div className="modal-attr" key={key}>
                    <span className="modal-attr-key">{key.replace(/_/g, " ")}</span>
                    <span className="modal-attr-value">
                      {Array.isArray(value) ? value.join(", ") : String(value)}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <section className="modal-section">
              <h3>Similar items</h3>
              {detail.similar_items.length === 0 && <p className="muted">No similar items found.</p>}
              <div className="modal-item-row">
                {detail.similar_items.map((p) => (
                  <MiniProductCard key={p.id} product={p} subtext={`₹${p.price}`} onAddToCart={onAddMiniToCart} />
                ))}
              </div>
            </section>

            <section className="modal-section">
              <h3>You might also like</h3>
              {detail.recommendations.length === 0 && (
                <p className="muted">No cross-sell data for this product yet.</p>
              )}
              <div className="modal-item-row">
                {detail.recommendations.map((r) => (
                  <MiniProductCard
                    key={r.product_id}
                    product={{ id: r.product_id, name: r.name, price: r.price, image_url: r.image_url, category: r.category }}
                    subtext={`₹${r.price} · ${Math.round(r.co_purchase_rate * 100)}% co-purchased`}
                    onAddToCart={onAddMiniToCart}
                  />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
