import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import Results from './Results.jsx'
import MerchantDashboard from './MerchantDashboard.jsx'

// Day 12: /merchant is a separate page from the shopper chat UI, not a
// client-side route within it — plain pathname check, no router dependency
// needed for static entry points. /results (the chat/checkout page) added
// the same way: App.jsx is now a pure landing page that hands off to it.
const path = window.location.pathname
const Page = path.startsWith('/merchant')
  ? MerchantDashboard
  : path.startsWith('/results')
    ? Results
    : App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Page />
  </StrictMode>,
)
