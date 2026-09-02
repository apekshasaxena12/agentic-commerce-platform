// Frontend-only cart state — sessionStorage-backed, no backend order
// created until an item actually reaches checkout (see Results.jsx's
// proceedToPayment). Plain data helpers only; component state (useState)
// owns the live copy, these just persist/derive from it.

const CART_KEY = "shopfront_cart";

export function loadCart() {
  try {
    const raw = sessionStorage.getItem(CART_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveCart(items) {
  try {
    sessionStorage.setItem(CART_KEY, JSON.stringify(items));
  } catch {
    // sessionStorage unavailable (private mode, etc.) — cart just won't persist across reloads
  }
}

export function cartCount(items) {
  return items.reduce((sum, item) => sum + item.quantity, 0);
}

export function cartTotal(items) {
  return items.reduce((sum, item) => sum + item.price * item.quantity, 0);
}
