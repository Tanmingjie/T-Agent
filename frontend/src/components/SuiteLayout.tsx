import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { NavLink, Outlet, useParams, useNavigate, Link } from "react-router-dom";
import { apiGet } from "../api/client";
import { ListChecks, History, BarChart3, Settings, ChevronLeft } from "lucide-react";
import IconRail from "./IconRail";
import { setVersionId } from "../lib/session";
import { useSuiteRun } from "../hooks/useSuiteRun";

export type SuiteRun = ReturnType<typeof useSuiteRun>;

// 执行状态(含高频 statuses + SSE)放在**只包住 Outlet** 的 RunProvider 里,经 context 下发。
// 关键:RunProvider 的 children(<Outlet/>)由 SuiteLayout 创建,而 SuiteLayout **不持有** run
// → step_change 高频更新只重渲染 RunProvider 自身;children 引用不变,React 跳过整棵 Outlet
// 子树,**仅 useContext 的消费者(读 statuses 的用例页)重渲染**。侧栏/面包屑彻底不参与。
const RunCtx = createContext<SuiteRun | null>(null);

export function useSuiteRunCtx(): SuiteRun {
  const r = useContext(RunCtx);
  if (!r) throw new Error("useSuiteRunCtx 必须在 SuiteLayout(RunProvider)内使用");
  return r;
}

function RunProvider({
  suiteId,
  children,
}: {
  suiteId?: string;
  children: ReactNode;
}) {
  const run = useSuiteRun(suiteId);
  // 离开任务工作区 / 切换任务(suiteId 变)时关 SSE;切 tab(children 变、suiteId 不变)不关。
  useEffect(() => () => run.stop(), [suiteId]); // eslint-disable-line react-hooks/exhaustive-deps
  // 进入任务时:若该任务有**正在执行**的 run(退出执行页/回首页再进来),自动重连其 SSE,
  // 从 run_event 表重放进度 → 不再「再点进去就空白卡执行中」。
  useEffect(() => {
    if (!suiteId) return;
    let cancelled = false;
    apiGet<{ id: string; status: string }[]>(`/suites/${suiteId}/runs`)
      .then((runs) => {
        if (cancelled) return;
        const active = (runs || []).find((r) => r.status === "running");
        if (active) run.resume(active.id);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [suiteId]); // eslint-disable-line react-hooks/exhaustive-deps
  return <RunCtx.Provider value={run}>{children}</RunCtx.Provider>;
}

interface SuiteInfo {
  name: string;
  base_url: string;
  version_id?: string;
  cases?: unknown[];
  runs?: unknown[];
}

const navGroups = [
  {
    label: "Overview",
    links: [
      { to: "", label: "用例", icon: ListChecks, end: true },
      { to: "history", label: "执行历史", icon: History, end: false },
      { to: "reports", label: "报告", icon: BarChart3, end: false },
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
    if (!id) return;
    apiGet<SuiteInfo>(`/suites/${id}`)
      .then((s) => {
        setSuite(s);
        // 把该任务所属版本写回 session,返回测试任务页时下拉自动定位到它。
        if (s.version_id) setVersionId(s.version_id);
      })
      .catch(() => {});
  }, [id]);

  // 返回测试任务列表(版本由 session 记忆,任务列表页下拉据此定位)。
  const backTo = "/tasks";

  return (
    <div className="flex h-screen bg-white text-surface-900">
      <IconRail />
      {/* Suite-scoped sidebar */}
      <aside className="w-60 bg-white border-r border-gray-200 flex flex-col shrink-0">
        <div className="px-4 h-14 flex items-center border-b border-gray-200">
          <button
            onClick={() => navigate(backTo)}
            className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-surface-900 transition-colors min-w-0"
          >
            <ChevronLeft size={16} className="shrink-0" />
            <span className="font-medium truncate">{suite?.name ?? "测试任务"}</span>
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

      <main className="flex-1 overflow-auto bg-canvas">
        {/* Breadcrumb */}
        <div className="h-12 border-b border-gray-200 bg-white px-8 flex items-center text-sm text-gray-500">
          <Link to={backTo} className="hover:text-surface-900 transition-colors">
            测试任务
          </Link>
          <span className="mx-2 text-gray-300">/</span>
          <span className="text-surface-900 font-medium truncate">
            {suite?.name ?? "…"}
          </span>
        </div>
        <div className="px-8 py-7">
          <RunProvider suiteId={id}>
            <Outlet context={{ suite }} />
          </RunProvider>
        </div>
      </main>
    </div>
  );
}
