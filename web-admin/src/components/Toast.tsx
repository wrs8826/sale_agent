import React from 'react'
import { CheckCircle, XCircle, AlertTriangle, X } from 'lucide-react'
import type { ToastItem } from '../types'

const icons = {
  success: <CheckCircle size={16} className="text-green-500 shrink-0" />,
  error:   <XCircle    size={16} className="text-red-500 shrink-0" />,
  warning: <AlertTriangle size={16} className="text-amber-500 shrink-0" />,
}

export const ToastContainer: React.FC<{ toasts: ToastItem[] }> = ({ toasts }) => (
  <div className="fixed top-5 right-5 z-50 flex flex-col gap-2 pointer-events-none">
    {toasts.map(t => (
      <div
        key={t.id}
        className="flex items-center gap-2.5 bg-white border border-[#e5e5e5] rounded-xl shadow-lg px-4 py-3 min-w-[260px] max-w-[380px] pointer-events-auto animate-slide-in"
        style={{ animation: 'slideIn .2s ease' }}
      >
        {icons[t.type]}
        <span className="text-sm text-[#171717] flex-1">{t.message}</span>
      </div>
    ))}
  </div>
)

// inject keyframe via style tag
const style = document.createElement('style')
style.textContent = `@keyframes slideIn { from { opacity:0; transform:translateX(16px) } to { opacity:1; transform:none } }`
document.head.appendChild(style)
