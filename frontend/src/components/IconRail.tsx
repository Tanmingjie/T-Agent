import { NavLink } from "react-router-dom";
import { Layers, BookOpen, Zap } from "lucide-react";

const items = [
  { to: "/suites", icon: Layers, label: "测试套件" },
  { to: "/vocabulary", icon: BookOpen, label: "词汇表" },
];

export default function IconRail() {
  return (
    <aside className="w-14 bg-gray-50 border-r border-gray-200 flex flex-col items-center py-3 shrink-0">
      <NavLink
        to="/suites"
        className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center mb-4"
        title="T-Agent"
      >
        <Zap size={18} className="text-white" />
      </NavLink>
      <nav className="flex flex-col gap-1">
        {items.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            title={label}
            className={({ isActive }) =>
              `w-10 h-10 rounded-lg flex items-center justify-center transition-colors ${
                isActive
                  ? "bg-brand-50 text-brand-700"
                  : "text-gray-400 hover:text-surface-900 hover:bg-gray-100"
              }`
            }
          >
            <Icon size={19} />
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
