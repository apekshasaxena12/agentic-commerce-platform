import { useEffect, useState } from "react";
import "./App.css";
import { SendIcon } from "./icons.jsx";

// The landing page is a pure entry point — it never talks to the
// pipeline itself. Submitting a query here hands a payload to the results
// page via sessionStorage and navigates there; the results page acts on
// it once its own WebSocket connects. See Results.jsx.
//
// Two kinds of payload:
//   - "message": free text (typed, a suggestion chip, or a category pill's
//     canned phrase) — goes through the normal chat pipeline, same as
//     before.
//   - "browse": a real structured filter (currently just gender, since
//     that's the one dimension every product now has cleanly) — sent
//     straight to search_products, bypassing intent classification
//     entirely, so it can't degrade into a canned-phrase guess.
const PENDING_QUERY_KEY = "shopfront_pending_query";

function goToResults(text) {
  sessionStorage.setItem(PENDING_QUERY_KEY, JSON.stringify({ kind: "message", text }));
  window.location.href = "/results";
}

function goToResultsBrowse(label, filters) {
  sessionStorage.setItem(PENDING_QUERY_KEY, JSON.stringify({ kind: "browse", label, filters }));
  window.location.href = "/results";
}

// "Rewrites what you can ask" — types out each example query, pauses,
// deletes it, and moves to the next, in place of a static placeholder.
// Unmounts whenever the real input has text (see !input in the JSX below),
// so it never overlaps what the user is typing.
function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function TypewriterPlaceholder({ queries }) {
  const [text, setText] = useState(() => (prefersReducedMotion() ? queries[0] : ""));

  useEffect(() => {
    if (prefersReducedMotion()) {
      return;
    }
    const state = { queryIndex: 0, charIndex: 0, deleting: false };
    let timeoutId;
    const tick = () => {
      const current = queries[state.queryIndex];
      if (!state.deleting) {
        state.charIndex += 1;
        setText(current.slice(0, state.charIndex));
        if (state.charIndex === current.length) {
          state.deleting = true;
          timeoutId = setTimeout(tick, 1500);
          return;
        }
        timeoutId = setTimeout(tick, 35);
      } else {
        state.charIndex -= 1;
        setText(current.slice(0, state.charIndex));
        if (state.charIndex === 0) {
          state.deleting = false;
          state.queryIndex = (state.queryIndex + 1) % queries.length;
          timeoutId = setTimeout(tick, 400);
          return;
        }
        timeoutId = setTimeout(tick, 18);
      }
    };
    timeoutId = setTimeout(tick, 500);
    return () => clearTimeout(timeoutId);
  }, [queries]);

  return (
    <div className="chatbox-placeholder" aria-hidden="true">
      {text}
      <span className="chatbox-caret" />
    </div>
  );
}

// A separate toggle group from category, not a replacement: every product
// now has a real gender attribute (unlike the 9-value category enum, which
// doesn't collapse into one clean UI dimension), so this filters on it
// directly via the "browse" WS path above instead of a canned phrase.
const GENDER_TOGGLES = [
  { key: "women", label: "Women", filters: { gender: "women" } },
  { key: "men", label: "Men", filters: { gender: "men" } },
];

const SUGGESTION_CHIPS = [
  "Waterproof trail running shoes under ₹6,000",
  "Compression socks for long runs",
  "Insoles for flat feet",
  "Running shorts with a zip pocket",
  "Windproof jacket for early morning runs",
  "GPS running watch under ₹5,000",
];

export default function App() {
  const [input, setInput] = useState("");
  const [activeGender, setActiveGender] = useState(null);

  function submitQuery(text) {
    const trimmed = text.trim();
    if (!trimmed) return;
    goToResults(trimmed);
  }

  function selectGender(toggle) {
    setActiveGender(toggle.key);
    goToResultsBrowse(toggle.label, toggle.filters);
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
      </header>

      <section className="hero">
        <h1 className="hero-headline">Gear up for your next run.</h1>

        <div className="hero-glow-zone">
          <div className="category-toggle" role="tablist" aria-label="Shopping for">
            {GENDER_TOGGLES.map((toggle) => (
              <button
                key={toggle.key}
                type="button"
                role="tab"
                aria-selected={activeGender === toggle.key}
                className={`category-pill ${activeGender === toggle.key ? "active" : ""}`}
                onClick={() => selectGender(toggle)}
              >
                {toggle.label}
              </button>
            ))}
          </div>

          <div className="hero-chatbox">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitQuery(input)}
              aria-label="Ask the catalog"
            />
            {!input && <TypewriterPlaceholder queries={SUGGESTION_CHIPS} />}
            <button type="button" className="chatbox-send" onClick={() => submitQuery(input)} aria-label="Send">
              <SendIcon />
            </button>
          </div>
        </div>

        <div className="chip-row">
          {SUGGESTION_CHIPS.map((chip) => (
            <button key={chip} type="button" className="suggestion-chip" onClick={() => goToResults(chip)}>
              {chip}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
