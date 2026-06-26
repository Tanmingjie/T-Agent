import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Layers,
  BookOpen,
  Settings,
  Zap,
  UserRound,
  BookMarked,
  BookText,
} from "lucide-react";

// 项目级导航。作用域分两组:
//   概览 / 测试任务(版本降为任务页内的下拉)走上半组;项目级配置(词汇表/Skills/设置)走下半组。
//   报告不在此 —— 它是任务级,挂在测试任务工作区(SuiteLayout)内。
const groups = [
  {
    label: "项目",
    links: [
      { to: "/", label: "概览", icon: LayoutDashboard, end: true },
      { to: "/tasks", label: "测试任务", icon: Layers, end: false },
    ],
  },
  {
    label: "配置",
    links: [
      { to: "/vocabulary", label: "词汇表", icon: BookOpen, end: false },
      { to: "/skills", label: "Skills", icon: BookMarked, end: false },
      { to: "/case-spec", label: "用例规范", icon: BookText, end: false },
      { to: "/settings", label: "设置", icon: Settings, end: false },
    ],
  },
];

export default function Sidebar() {
  return (
    <aside className="w-60 bg-white border-r border-gray-200 flex flex-col shrink-0">
      {/* Brand */}
      <div className="px-5 h-14 flex items-center border-b border-gray-200">
        <NavLink to="/" className="flex items-center gap-2.5">
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
              {g.links.map(({ to, label, icon: Icon, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
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

      {/* 身份占位:M4 接 IDaaS 后换成真实用户 + 角色徽章 */}
      <div className="px-4 py-3 border-t border-gray-200 flex items-center gap-2 text-xs text-gray-400">
        <UserRound size={14} />
        <span className="truncate">未接入身份(IDaaS 待集成)</span>
      </div>
    </aside>
  );
}
