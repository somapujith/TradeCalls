import { NavLink, Route, Routes } from 'react-router-dom'
import CallsPage from './pages/CallsPage.jsx'
import BacktestResultsPage from './pages/BacktestResultsPage.jsx'
import DashboardPage from './pages/DashboardPage.jsx'

function NavBar() {
  const linkClass = ({ isActive }) =>
    `rounded-md px-3 py-2 text-sm font-medium transition-colors ${
      isActive ? 'bg-slate-800 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-white'
    }`

  return (
    <nav className="border-b border-slate-800 bg-slate-950">
      <div className="mx-auto flex max-w-6xl items-center gap-2 px-4 py-3">
        <span className="mr-4 text-lg font-semibold text-white">TradeCalls</span>
        <NavLink to="/" end className={linkClass}>
          Calls
        </NavLink>
        <NavLink to="/backtests" className={linkClass}>
          Backtest Results
        </NavLink>
      </div>
    </nav>
  )
}

export default function App() {
  return (
    <div className="min-h-screen bg-slate-950">
      <NavBar />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<CallsPage />} />
          <Route path="/backtests" element={<BacktestResultsPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
        </Routes>
      </main>
    </div>
  )
}
