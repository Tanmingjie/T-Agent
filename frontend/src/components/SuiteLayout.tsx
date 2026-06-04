import { useEffect, useState } from "react";
import { NavLink, Outlet, useParams, useNavigate, Link } from "react-router-dom";
import { apiGet } from "../api/client";
import { ListChecks, History, Settings, ChevronLeft } from "lucide-react";
import IconRail from "./IconRail";

interface SuiteInfo {
  name: string;
  base_url: string;
  cases?: unknown[];
  runs?: unknown[];
}

const navGroups = [
  {
    label: "Overview",
    links: [
      { to: "", label: "用例", icon: ListChecks, end: true },
      { to: "history", label: "执行历史", icon: History, end: false },
    ],
  },
  {
    label: "Settings",
    links: [{ to: "settings", label: "设置", icon: Settings, end: false }],
  },
];

export default function SuiteLayout() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [suite, setSuite] = useState<SuiteInfo | null>(null);

  useEffect(() => {
    if (id) apiGet<SuiteInfo>(`/suites/${id}`).then(setSuite).catch(() => {});
  }, [id]);

  return (
    <div className="flex h-screen bg-white text-surface-900">
      <IconRail />
      {/* Suite-scoped sidebar */}
      <aside className="w-60 bg-white border-r border-gray-200 flex flex-col shrink-0">
        <div className="px-4 h-14 flex items-center border-b border-gray-200">
          <button
            onClick={() => navigate("/suites")}
            className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-surface-900 transition-colors min-w-0"
          >
            <ChevronLeft size={16} className="shrink-0" />
            <span className="font-medium truncate">{suite?.name ?? "套件"}</span>
          </button>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-6 overflow-y-auto">
          {navGroups.map((g) => (
            <div key={g.label}>
              <p className="px-3 mb-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-400">
                {g.label}
              </p>
              <div className="space-y-0.5">
                {g.links.map(({ to, label, icon: Icon, end }) => (
                  <NavLink
                    key={to || "index"}
                    to={to ? `/suites/${id}/${to}` : `/suites/${id}`}
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

        <div className="px-5 py-4 border-t border-gray-200 text-xs text-gray-400">
          AI Test Automation
        </div>
      </aside>

      <main className="flex-1 overflow-auto bg-gray-50/40">
        {/* Breadcrumb */}
        <div className="h-12 border-b border-gray-200 bg-white px-8 flex items-center text-sm text-gray-500">
          <Link to="/suites" className="hover:text-surface-900 transition-colors">
            测试套件
          </Link>
          <span className="mx-2 text-gray-300">/</span>
          <span className="text-surface-900 font-medium truncate">
            {suite?.name ?? "…"}
          </span>
        </div>
        <div className="px-8 py-7">
          <Outlet context={{ suite }} />
        </div>
      </main>
    </div>
  );
}
