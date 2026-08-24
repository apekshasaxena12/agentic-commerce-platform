import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import MerchantDashboard from './MerchantDashboard.jsx'

// Day 12: /merchant is a separate page from the shopper chat UI, not a
// client-side route within it — plain pathname check, no router dependency
// needed for two static entry points.
const Page = window.location.pathname.startsWith('/merchant') ? MerchantDashboard : App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Page />
  </StrictMode>,
)
