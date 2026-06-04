import { NavLink } from "react-router-dom";
import { Layers, BookOpen, Zap } from "lucide-react";

const groups = [
  {
    label: "Dashboard",
    links: [{ to: "/suites", label: "测试套件", icon: Layers }],
  },
  {
    label: "配置",
    links: [{ to: "/vocabulary", label: "词汇表", icon: BookOpen }],
  },
];

export default function Sidebar() {
  return (
    <aside className="w-60 bg-white border-r border-gray-200 flex flex-col shrink-0">
      {/* Brand */}
      <div className="px-5 h-14 flex items-center border-b border-gray-200">
        <NavLink to="/suites" className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-brand-600 flex items-center justify-center">
            <Zap size={16} className="text-white" />
          </div>
          <span className="font-semibold text-[15px] tracking-tight text-surface-900">
            T-Agent
          </span>
        </NavLink>
      </div>

      {/* Nav groups */}
      <nav className="flex-1 px-3 py-4 space-y-6 overflow-y-auto">
        {groups.map((g) => (
          <div key={g.label}>
            <p className="px-3 mb-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-400">
              {g.label}
            </p>
            <div className="space-y-0.5">
              {g.links.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                      isActive
                        ? "bg-gray-100 text-surface-900 font-medium"
                        : "text-gray-600 hover:text-surface-900 hover:bg-gray-50"
                    }`
                  }
                >
                  <Icon size={17} />
                  <span>{label}</span>
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-gray-200 text-xs text-gray-400">
        AI Test Automation
      </div>
    </aside>
  );
}
