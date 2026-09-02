import { useState } from "react";

const API_BASE = import.meta.env.VITE_MERCHANT_API_BASE || "http://localhost:8765/merchant";

export default function MerchantLogin({ onLoggedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        setError("Incorrect email or password.");
        return;
      }
      onLoggedIn(await res.json());
    } catch {
      setError("Could not reach the merchant API — is mcp_server.server --http running on :8765?");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="merchant-login">
      <form className="merchant-login-card" onSubmit={handleSubmit}>
        <h1>Merchant sign in</h1>
        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <div className="banner-error">{error}</div>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
