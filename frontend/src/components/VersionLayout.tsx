import { useEffect, useState } from "react";
import { NavLink, Outlet, useParams, useNavigate, Link } from "react-router-dom";
import { Layers, BarChart3, ChevronLeft } from "lucide-react";
import IconRail from "./IconRail";
import { apiGet } from "../api/client";
import { getProjectId, setVersionId } from "../lib/session";

interface Version {
  id: string;
  name: string;
}

// 版本工作区:套件与报告并列(同为版本级)。进入即把当前版本写入 session,
// 供版本级请求(套件列表 / 报告)作用域使用。
export default function VersionLayout() {
  const { vid } = useParams<{ vid: string }>();
  const navigate = useNavigate();
  const pid = getProjectId();
  const [version, setVersion] = useState<Version | null>(null);
  const [projectName, setProjectName] = useState("");

  useEffect(() => {
    if (vid) setVersionId(vid);
  }, [vid]);

  useEffect(() => {
    if (!pid) return;
    apiGet<{ name: string }>(`/projects/${pid}`)
      .then((p) => setProjectName(p.name))
      .catch(() => {});
    apiGet<Version[]>(`/projects/${pid}/versions`)
      .then((vs) => setVersion(vs.find((v) => v.id === vid) ?? null))
      .catch(() => {});
  }, [pid, vid]);

  const links = [
    { to: "", label: "套件", icon: Layers, end: true },
    { to: "reports", label: "报告", icon: BarChart3, end: false },
  ];

  return (
    <div className="flex h-screen bg-white text-surface-900">
      <IconRail />
      <aside className="w-60 bg-white border-r border-gray-200 flex flex-col shrink-0">
        <div className="px-4 h-14 flex items-center border-b border-gray-200">
          <button
            onClick={() => navigate("/versions")}
            className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-surface-900 transition-colors min-w-0"
          >
            <ChevronLeft size={16} className="shrink-0" />
            <span className="font-medium truncate">{version?.name ?? "版本"}</span>
          </button>
        </div>

        <nav className="flex-1 px-3 py-4 overflow-y-auto">
          <p className="px-3 mb-1.5 text-[11px] font-medium uppercase tracking-wider text-gray-400">
            版本 · {version?.name ?? "…"}
          </p>
          <div className="space-y-0.5">
            {links.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to || "index"}
                to={to ? `/versions/${vid}/${to}` : `/versions/${vid}`}
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
        </nav>

        <div className="px-5 py-4 border-t border-gray-200 text-xs text-gray-400">
          AI Test Automation
        </div>
      </aside>

      <main className="flex-1 overflow-auto bg-canvas">
        <div className="h-12 border-b border-gray-200 bg-white px-8 flex items-center text-sm text-gray-500">
          <span className="text-gray-500">{projectName || "项目"}</span>
          <span className="mx-2 text-gray-300">/</span>
          <Link to="/versions" className="hover:text-surface-900 transition-colors">
            版本
          </Link>
          <span className="mx-2 text-gray-300">/</span>
          <span className="text-surface-900 font-medium truncate">
            {version?.name ?? "…"}
          </span>
        </div>
        <div className="px-8 py-7">
          <Outlet context={{ versionId: vid, version }} />
        </div>
      </main>
    </div>
  );
}
