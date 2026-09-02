// Inline line-art icons for the seeded product categories (see
// db/seed.py — running_shoes, insoles, socks, apparel_top, apparel_bottom,
// outerwear, accessories, hydration, wearable_tech). All inline SVG, no
// network fetch, so the catalog renders identically offline.

const ICON_PATHS = {
  running_shoes: (
    <>
      <path d="M3 16.5c0-1 .4-1.8 1.2-2.4l2.1-1.6c.3-.2.5-.6.5-1V8.2c0-.5.5-.9 1-.7l2.7 1c1.6.6 2.9 1.8 3.6 3.3l.4.9 3.6 1.2c1 .3 1.9 1.2 1.9 2.2v.4H3z" />
      <path d="M3 16.5h17" />
      <path d="M8.7 9.5l2 2M6 11.3h2.4" />
    </>
  ),
  insoles: (
    <>
      <path d="M9.4 3c-1.5 0-2.6 1.3-2.6 2.9 0 1.1.5 1.8.5 2.9 0 1.5-1.4 2.3-1.9 4-.6 2.2-.5 4.8 1 6.5.9 1.1 2.4 1.7 3.8 1.7 2.7 0 4.3-1.9 4.6-4.3.3-2.3-.5-3.8-1-5.7-.5-2.3-.3-3.6.2-5.3.4-1.5-.4-2.9-1.9-3.3-1-.3-1.9 0-2.7.6z" />
      <circle cx="9.6" cy="5.8" r="0.55" fill="currentColor" stroke="none" />
    </>
  ),
  socks: (
    <>
      <path d="M9 3h5v7.3l3.7 5.2c.9 1.3.2 3.2-1.4 3.5l-4.5.9c-1.7.3-3.2-1-3.2-2.7V6.4C8.6 4.9 8.7 3.8 9 3z" />
      <path d="M9 8.3h5" />
    </>
  ),
  apparel_top: (
    <>
      <path d="M8 4 4 6.5 5.5 10 8 8.8V20h8V8.8l2.5 1.2L20 6.5 16 4c-.6 1.2-2 2-4 2s-3.4-.8-4-2z" />
    </>
  ),
  apparel_bottom: (
    <>
      <path d="M5 4h14l.4 5.3-1.9.3-.9-4.1H13l.35 5-1.35 8.5h-1L10 10.3 8.65 18.9h-1L6.4 10.4l-.9 4.1-1.9-.3z" />
    </>
  ),
  outerwear: (
    <>
      <path d="M8 4 4 6.5 5.5 10 8 8.8V20h8V8.8l2.5 1.2L20 6.5 16 4c-.6 1.2-2 2-4 2s-3.4-.8-4-2z" />
      <path d="M12 6.4V20" />
      <path d="M9.7 4.4 12 6.8l2.3-2.4" />
    </>
  ),
  accessories: (
    <>
      <path d="M4.5 14a7.5 7.5 0 0 1 15 0" />
      <path d="M3 14h16.8c.9 0 1.1-1.1.3-1.5l-3.4-1.9" />
      <path d="M12 6.5V4.3" />
    </>
  ),
  hydration: (
    <>
      <path d="M10 2h4v2.3c0 .5.2 1 .5 1.3l1 1.1c.3.4.5.9.5 1.4V20a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2V8.1c0-.5.2-1 .5-1.4l1-1.1c.3-.4.5-.8.5-1.3z" />
      <path d="M8 12.3h8" />
    </>
  ),
  wearable_tech: (
    <>
      <rect x="7" y="7" width="10" height="10" rx="2.2" />
      <path d="M9.4 7V4.2a1 1 0 0 1 1-1h3.2a1 1 0 0 1 1 1V7" />
      <path d="M9.4 17v2.8a1 1 0 0 0 1 1h3.2a1 1 0 0 0 1-1V17" />
      <circle cx="15.6" cy="9.6" r="0.5" fill="currentColor" stroke="none" />
    </>
  ),
};

const FALLBACK = (
  <>
    <rect x="4" y="4" width="16" height="16" rx="3" />
    <path d="M9 12h6" />
  </>
);

export default function CategoryIcon({ category, className }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {ICON_PATHS[category] || FALLBACK}
    </svg>
  );
}

// Shared by the landing hero's chat box and the results page's pinned
// composer — same send affordance in both places.
export function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 19V5" />
      <path d="M5 12l7-7 7 7" />
    </svg>
  );
}

// Replaces the removed Sign in button, top-right, on the shopper pages.
export function CartIcon() {
  return (
    <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="9" cy="20" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="18" cy="20" r="1.3" fill="currentColor" stroke="none" />
      <path d="M2.5 3h2.2l2.1 11.4a2 2 0 0 0 2 1.6h8.4a2 2 0 0 0 2-1.6L21 7H6" />
    </svg>
  );
}
