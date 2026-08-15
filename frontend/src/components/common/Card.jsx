export default function Card({ children, className = '' }) {
  return (
    <div className={`rounded-lg border border-slate-800 bg-slate-900 p-4 shadow-sm ${className}`}>
      {children}
    </div>
  )
}
