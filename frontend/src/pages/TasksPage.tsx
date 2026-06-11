// 测试任务(项目落地页之一):版本 → 测试任务的树。
// 版本作为可展开的分组行,展开后列出该版本下的测试任务(项目→版本→任务的层级直观呈现)。
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiPost, apiDelete } from "../api/client";
import { withProject, getProjectId, getVersionId, setVersionId } from "../lib/session";
import {
  Plus,
  Trash2,
  Play,
  Layers,
  Search,
  ChevronRight,
  GitBranch,
} from "lucide-react";

interface Version {
  id: string;
  name: string;
}
interface LastRun {
  id: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
}
interface Suite {
  id: string;
  name: string;
  base_url: string;
  version_id?: string;
  updated_at?: string | null;
  case_count?: number;
  last_run?: LastRun | null;
}

function fmtDate(v?: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function LastRunCell({ run }: { run?: LastRun | null }) {
  if (!run) return <span className="text-gray-300">未执行</span>;
  const ok = run.status === "completed" && run.failed_cases === 0;
  const running = run.status === "running";
  const dot = running ? "bg-blue-500" : ok ? "bg-brand-500" : "bg-red-500";
  const text = running ? "text-blue-600" : ok ? "text-brand-700" : "text-red-600";
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`w-2 h-2 rounded-full ${dot}`} />
      <span className={text}>
        {run.passed_cases}/{run.total_cases} 通过
      </span>
    </span>
  );
}

export default function TasksPage() {
  const pid = getProjectId();
  const [versions, setVersions] = useState<Version[]>([]);
  const [suites, setSuites] = useState<Suite[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [createFor, setCreateFor] = useState<string | null>(null); // 在哪个版本下新建
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!pid) return;
    apiGet<Version[]>(`/projects/${pid}/versions`)
      .then((vs) => {
        setVersions(vs);
        // 默认展开:上次访问的版本(或第一个),其余收起。
        const stored = getVersionId();
        const pick = vs.find((v) => v.id === stored)?.id || vs[0]?.id;
        if (pick) setExpanded(new Set([pick]));
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [pid]);

  async function load() {
    if (!pid) return;
    try {
      // 一次取项目下全部任务(带状态),前端按版本分组成树。
      setSuites(await apiGet<Suite[]>(withProject("/suites?with_status=true")));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  useEffect(() => {
    load();
  }, [pid]);

  function toggle(vid: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(vid)) next.delete(vid);
      else {
        next.add(vid);
        setVersionId(vid); // 记住最近展开的版本
      }
      return next;
    });
  }

  function openCreate(vid: string) {
    setCreateFor(vid);
    setName("");
    setBaseUrl("");
    setExpanded((prev) => new Set(prev).add(vid));
  }

  async function create(vid: string) {
    try {
      await apiPost("/suites", {
        name,
        base_url: baseUrl,
        project_id: pid,
        version_id: vid,
      });
      setCreateFor(null);
      setName("");
      setBaseUrl("");
      load();
    } catch (e) {
      alert("创建失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function remove(id: string) {
    if (!window.confirm("确认删除此测试任务？此操作不可恢复。")) return;
    try {
      await apiDelete(`/suites/${id}`);
      load();
    } catch (e) {
      alert("删除失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  // 按版本分组(过滤后)。搜索命中时自动算入,但展开与否仍由用户控制。
  const byVersion = useMemo(() => {
    const q = query.trim().toLowerCase();
    const m = new Map<string, Suite[]>();
    for (const s of suites) {
      if (
        q &&
        !s.name.toLowerCase().includes(q) &&
        !(s.base_url || "").toLowerCase().includes(q)
      )
        continue;
      const vid = s.version_id || "";
      if (!m.has(vid)) m.set(vid, []);
      m.get(vid)!.push(s);
    }
    return m;
  }, [suites, query]);

  if (!pid) {
    return (
      <div className="max-w-md mt-10 text-center">
        <h1 className="text-lg font-semibold text-surface-900 mb-2">未指定项目</h1>
        <p className="text-sm text-gray-500">
          请通过内网系统选择项目后进入，或在 URL 加 <code>?project=&lt;id&gt;</code>。
        </p>
      </div>
    );
  }

  // 搜索时自动展开所有有命中的版本。
  const isOpen = (vid: string) => (query.trim() ? true : expanded.has(vid));

  return (
    <div className="max-w-4xl">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-surface-900">测试任务</h1>
          <p className="text-sm text-gray-500 mt-1">
            按版本组织的测试任务。展开版本查看其下任务。版本由内网系统维护。
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-5 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* Search */}
      <div className="flex items-center gap-2 mb-3">
        <div className="relative">
          <Search
            size={15}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
          />
          <input
            className="w-64 border border-gray-300 rounded-md pl-9 pr-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
            placeholder="搜索任务…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <span className="text-xs text-gray-400 ml-auto">{versions.length} 个版本</span>
      </div>

      {/* 版本 → 任务 树 */}
      <div className="space-y-2">
        {versions.length === 0 && (
          <div className="bg-white border border-gray-200 rounded-lg px-5 py-12 text-center">
            <GitBranch size={32} className="mx-auto text-gray-300 mb-3" />
            <p className="text-sm text-gray-500">该项目暂无版本</p>
            <p className="text-xs text-gray-400 mt-1">版本在内网系统中创建后会出现在这里</p>
          </div>
        )}

        {versions.map((v) => {
          const tasks = byVersion.get(v.id) ?? [];
          const open = isOpen(v.id);
          return (
            <div
              key={v.id}
              className="bg-white border border-gray-200 rounded-lg overflow-hidden"
            >
              {/* 版本行 */}
              <div className="flex items-center px-4 py-3 hover:bg-gray-50/70 transition-colors">
                <button
                  onClick={() => toggle(v.id)}
                  className="flex items-center gap-2 flex-1 min-w-0 text-left"
                >
                  <ChevronRight
                    size={16}
                    className={`text-gray-400 transition-transform shrink-0 ${
                      open ? "rotate-90" : ""
                    }`}
                  />
                  <GitBranch size={15} className="text-gray-400 shrink-0" />
                  <span className="font-medium text-surface-900 truncate">{v.name}</span>
                  <span className="text-xs text-gray-400">
                    {tasks.length} 个任务
                  </span>
                </button>
                <button
                  onClick={() => openCreate(v.id)}
                  className="inline-flex items-center gap-1 text-xs text-brand-700 hover:bg-brand-50 px-2 py-1 rounded-md transition-colors shrink-0"
                  title="在此版本下新建测试任务"
                >
                  <Plus size={14} /> 新建任务
                </button>
              </div>

              {/* 新建表单(挂在该版本下) */}
              {createFor === v.id && (
                <div className="px-4 py-3 bg-gray-50/60 border-t border-gray-100">
                  <div className="grid sm:grid-cols-2 gap-3 mb-3">
                    <input
                      className="border border-gray-300 px-3 py-2 rounded-md text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
                      placeholder="任务名称"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      autoFocus
                    />
                    <input
                      className="border border-gray-300 px-3 py-2 rounded-md text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
                      placeholder="Base URL (e.g. https://example.com)"
                      value={baseUrl}
                      onChange={(e) => setBaseUrl(e.target.value)}
                    />
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => create(v.id)}
                      disabled={!name.trim()}
                      className="bg-brand-600 text-white px-4 py-1.5 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      创建
                    </button>
                    <button
                      onClick={() => setCreateFor(null)}
                      className="border border-gray-300 px-4 py-1.5 rounded-md text-sm text-gray-600 hover:bg-gray-50 transition-colors"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {/* 任务列表 */}
              {open && (
                <div className="border-t border-gray-100">
                  {tasks.length === 0 ? (
                    <p className="px-12 py-6 text-sm text-gray-400">
                      {query ? "无匹配任务" : "该版本下还没有测试任务"}
                    </p>
                  ) : (
                    <table className="w-full text-sm">
                      <tbody>
                        {tasks.map((s) => (
                          <tr
                            key={s.id}
                            onClick={() => navigate(`/suites/${s.id}`)}
                            className="border-b border-gray-50 last:border-0 hover:bg-gray-50/70 cursor-pointer transition-colors group"
                          >
                            <td className="pl-12 pr-5 py-3">
                              <div className="flex items-center gap-3">
                                <div className="w-7 h-7 rounded-md bg-brand-50 flex items-center justify-center shrink-0">
                                  <Layers size={15} className="text-brand-600" />
                                </div>
                                <span className="font-medium text-surface-900 group-hover:text-brand-700 transition-colors">
                                  {s.name}
                                </span>
                              </div>
                            </td>
                            <td className="px-5 py-3 text-gray-500 whitespace-nowrap">
                              {s.case_count ?? 0} 用例
                            </td>
                            <td className="px-5 py-3 whitespace-nowrap">
                              <LastRunCell run={s.last_run} />
                            </td>
                            <td className="px-5 py-3 text-gray-400 whitespace-nowrap">
                              {fmtDate(s.updated_at)}
                            </td>
                            <td className="px-5 py-3 w-px">
                              <div className="flex items-center justify-end gap-1">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    navigate(`/suites/${s.id}`);
                                  }}
                                  className="p-1.5 rounded-md text-gray-400 hover:text-brand-600 hover:bg-brand-50 transition-colors"
                                  title="打开并执行"
                                >
                                  <Play size={15} />
                                </button>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    remove(s.id);
                                  }}
                                  className="p-1.5 rounded-md text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                                  title="删除"
                                >
                                  <Trash2 size={15} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
