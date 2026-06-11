import { NavLink } from "react-router-dom";
import { LayoutDashboard, Layers, BookOpen, Settings, Zap } from "lucide-react";

// 深层工作区(测试任务)左侧的细图标轨,用于快速跳回项目级页面。
const items = [
  { to: "/", icon: LayoutDashboard, label: "概览", end: true },
  { to: "/tasks", icon: Layers, label: "测试任务", end: false },
  { to: "/vocabulary", icon: BookOpen, label: "词汇表", end: false },
  { to: "/settings", icon: Settings, label: "设置", end: false },
];

export default function IconRail() {
  return (
    <aside className="w-14 bg-gray-50 border-r border-gray-200 flex flex-col items-center py-3 shrink-0">
      <NavLink
        to="/"
        className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center mb-4"
        title="T-Agent"
      >
        <Zap size={18} className="text-white" />
      </NavLink>
      <nav className="flex flex-col gap-1">
        {items.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
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
