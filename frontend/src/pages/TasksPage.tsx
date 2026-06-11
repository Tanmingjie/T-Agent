// 测试任务(项目落地页之一):版本下拉 + 该版本下的测试任务表。
// 版本层从独立页面降为这里的下拉(项目→[版本下拉]→任务);选中版本落 session 续存。
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiPost, apiDelete } from "../api/client";
import { withProject, getProjectId, getVersionId, setVersionId } from "../lib/session";
import { Plus, Trash2, Play, Layers, Search, GitBranch, ChevronDown } from "lucide-react";

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
  finished_at?: number | null;
  started_at?: number | null;
}
interface Suite {
  id: string;
  name: string;
  base_url: string;
  updated_at?: string | null;
  case_count?: number;
  last_run?: LastRun | null;
}

function fmtDate(v?: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  if (isNaN(d.getTime())) return v;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// 最近执行单元格:状态点 + 通过/总数。无执行记录显示「未执行」。
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
  const [versionId, setVid] = useState<string>(getVersionId());
  const [suites, setSuites] = useState<Suite[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  // 拉版本列表;选中版本缺省取 session 续存值,否则第一个。
  useEffect(() => {
    if (!pid) return;
    apiGet<Version[]>(`/projects/${pid}/versions`)
      .then((vs) => {
        setVersions(vs);
        const stored = getVersionId();
        const pick = vs.find((v) => v.id === stored)?.id || vs[0]?.id || "";
        setVid(pick);
        setVersionId(pick);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [pid]);

  async function loadSuites(vid: string) {
    if (!vid) {
      setSuites([]);
      return;
    }
    try {
      const path = `/suites?version_id=${encodeURIComponent(vid)}&with_status=true`;
      setSuites(await apiGet<Suite[]>(withProject(path)));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  useEffect(() => {
    loadSuites(versionId);
  }, [versionId]);

  function onPickVersion(vid: string) {
    setVid(vid);
    setVersionId(vid);
  }

  async function create() {
    try {
      await apiPost("/suites", {
        name,
        base_url: baseUrl,
        project_id: pid,
        version_id: versionId,
      });
      setShowCreate(false);
      setName("");
      setBaseUrl("");
      loadSuites(versionId);
    } catch (e) {
      alert("创建失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function remove(id: string) {
    if (!window.confirm("确认删除此测试任务？此操作不可恢复。")) return;
    try {
      await apiDelete(`/suites/${id}`);
      loadSuites(versionId);
    } catch (e) {
      alert("删除失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return suites;
    return suites.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.base_url || "").toLowerCase().includes(q),
    );
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

  return (
    <div>
      {/* Header:版本下拉 + 新建 */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-surface-900">测试任务</h1>
            {/* 版本下拉(版本层降为此选择器) */}
            <div className="relative">
              <GitBranch
                size={14}
                className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none"
              />
              <ChevronDown
                size={14}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none"
              />
              <select
                value={versionId}
                onChange={(e) => onPickVersion(e.target.value)}
                className="appearance-none border border-gray-300 rounded-md pl-7 pr-7 py-1.5 text-sm bg-white text-surface-900 focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500 cursor-pointer"
                title="切换版本"
              >
                {versions.length === 0 && <option value="">无版本</option>}
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-sm text-gray-500 mt-1">
            管理当前版本下的测试任务，上传用例并执行 AI 自动化测试。版本由内网系统维护。
          </p>
        </div>
        <button
          onClick={() => setShowCreate((v) => !v)}
          disabled={!versionId}
          className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-3.5 py-2 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <Plus size={16} /> 新建测试任务
        </button>
      </div>

      {error && (
        <div className="mb-5 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* Create form */}
      {showCreate && (
        <div className="mb-5 p-4 bg-white border border-gray-200 rounded-lg">
          <h3 className="font-medium text-surface-900 mb-3 text-sm">新建测试任务</h3>
          <div className="grid sm:grid-cols-2 gap-3 mb-3">
            <input
              className="border border-gray-300 px-3 py-2 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
              placeholder="任务名称"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <input
              className="border border-gray-300 px-3 py-2 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
              placeholder="Base URL (e.g. https://example.com)"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={create}
              disabled={!name.trim()}
              className="bg-brand-600 text-white px-4 py-1.5 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              创建
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="border border-gray-300 px-4 py-1.5 rounded-md text-sm text-gray-600 hover:bg-gray-50 transition-colors"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* Toolbar */}
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
        <span className="text-xs text-gray-400 ml-auto">共 {filtered.length} 个任务</span>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500">
              <th className="px-5 py-3 font-medium">名称</th>
              <th className="px-5 py-3 font-medium">用例数</th>
              <th className="px-5 py-3 font-medium">最近执行</th>
              <th className="px-5 py-3 font-medium">更新时间</th>
              <th className="px-5 py-3 font-medium w-px"></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-16 text-center">
                  <Layers size={36} className="mx-auto text-gray-300 mb-3" />
                  <p className="text-gray-500 text-sm mb-1">
                    {query
                      ? "没有匹配的任务"
                      : versionId
                        ? "该版本还没有测试任务"
                        : "请先选择一个版本"}
                  </p>
                  {!query && versionId && (
                    <p className="text-xs text-gray-400">
                      点击「新建测试任务」创建你的第一个测试任务
                    </p>
                  )}
                </td>
              </tr>
            )}
            {filtered.map((s) => (
              <tr
                key={s.id}
                onClick={() => navigate(`/suites/${s.id}`)}
                className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70 cursor-pointer transition-colors group"
              >
                <td className="px-5 py-3.5">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-md bg-brand-50 flex items-center justify-center shrink-0">
                      <Layers size={16} className="text-brand-600" />
                    </div>
                    <span className="font-medium text-surface-900 group-hover:text-brand-700 transition-colors">
                      {s.name}
                    </span>
                  </div>
                </td>
                <td className="px-5 py-3.5 text-gray-500">{s.case_count ?? 0}</td>
                <td className="px-5 py-3.5">
                  <LastRunCell run={s.last_run} />
                </td>
                <td className="px-5 py-3.5 text-gray-500">{fmtDate(s.updated_at)}</td>
                <td className="px-5 py-3.5">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate(`/suites/${s.id}`);
                      }}
                      className="p-1.5 rounded-md text-gray-400 hover:text-brand-600 hover:bg-brand-50 transition-colors"
                      title="打开并执行"
                    >
                      <Play size={16} />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        remove(s.id);
                      }}
                      className="p-1.5 rounded-md text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                      title="删除"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
