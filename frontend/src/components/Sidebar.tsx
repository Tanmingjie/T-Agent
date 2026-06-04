import { NavLink } from "react-router-dom";
import { Layers, BookOpen, Zap } from "lucide-react";

const links = [
  { to: "/suites", label: "Suites", icon: Layers },
  { to: "/vocabulary", label: "词汇表", icon: BookOpen },
];

export default function Sidebar() {
  return (
    <aside className="w-56 bg-surface-900 text-white flex flex-col shrink-0">
      {/* Brand */}
      <div className="px-5 py-4 border-b border-white/10">
        <NavLink to="/suites" className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-brand-500 flex items-center justify-center">
            <Zap size={18} className="text-white" />
          </div>
          <span className="font-bold text-lg tracking-tight">T-Agent</span>
        </NavLink>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                isActive
                  ? "bg-white/10 text-white font-medium"
                  : "text-gray-400 hover:text-white hover:bg-white/5"
              }`
            }
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-white/10 text-xs text-gray-500">
        AI Test Automation
      </div>
    </aside>
  );
}
