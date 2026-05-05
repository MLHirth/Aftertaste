import { HashRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useEffect } from 'react'

import { AccountControl, AuthGate, SessionStatus } from './auth/clerk'
import { useAuthStore } from './state/authStore'
import { DesktopAuthBridge } from './screens/DesktopAuthBridge'
import { Dashboard } from './screens/Dashboard'
import { Memory } from './screens/Memory'
import { Rules } from './screens/Rules'
import { Sources } from './screens/Sources'
import { TodayMix } from './screens/TodayMix'

const links = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/today', label: "Today's Mix" },
  { to: '/memory', label: 'Memory' },
  { to: '/rules', label: 'Rules' },
  { to: '/sources', label: 'Sources' },
]

function App() {
  const initDeepLinkListener = useAuthStore((state) => state.initDeepLinkListener)

  useEffect(() => {
    void initDeepLinkListener()
  }, [initDeepLinkListener])

  return (
    <HashRouter>
      <div className="app-shell">
        <header className="topbar">
          <div className="brand">
            <p className="kicker">Aftertaste</p>
            <h1>Taste Memory Assistant</h1>
          </div>
          <div className="topbar-right">
            <SessionStatus />
            <nav>
              {links.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                >
                  {link.label}
                </NavLink>
              ))}
            </nav>
            <AccountControl />
          </div>
        </header>

        <AuthGate>
          <AppRoutes />
        </AuthGate>
      </div>
    </HashRouter>
  )
}

function AppRoutes() {
  const refreshStatus = useAuthStore((state) => state.refreshStatus)

  useEffect(() => {
    void refreshStatus()
  }, [refreshStatus])

  return (
    <Routes>
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/today" element={<TodayMix />} />
      <Route path="/memory" element={<Memory />} />
      <Route path="/rules" element={<Rules />} />
      <Route path="/sources" element={<Sources />} />
      <Route path="/desktop-auth" element={<DesktopAuthBridge />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}

export default App
