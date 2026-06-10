import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, apiPost } from "../api/client";
import {
  Upload,
  Play,
  Search,
  FileSpreadsheet,
  Loader2,
  CheckCircle,
  XCircle,
  Clock,
  Wrench,
} from "lucide-react";
import Drawer from "../components/Drawer";
import CaseDrawerBody from "../components/CaseDrawerBody";
import PermissionDialog from "../components/PermissionDialog";
import { useSuiteRun, CaseRunStatus } from "../hooks/useSuiteRun";

interface PreconditionItem {
  text: string;
  type: string;
  hook_ref?: string | null;
  confidence?: number;
  confirmed_by_user?: boolean;
}

interface Case {
  id: string;
  name: string;
  steps: string[];
  preconditions: string[];
  expected: string[];
  precondition_items?: PreconditionItem[];
}

interface RunLite {
  id: string;
  started_at: number;
}

interface RunOverview {
  cases: { case_id: string; passed: boolean }[];
}

interface SuiteResp {
  name: string;
  base_url: string;
  cases: Case[];
  runs: RunLite[];
}

const STATUS_META: Record<
  CaseRunStatus,
  { label: string; icon: React.ReactNode; cls: string }
> = {
  pending: { label: "未执行", icon: <Clock size={14} />, cls: "text-gray-400" },
  running: {
    label: "执行中",
    icon: <Loader2 size={14} className="animate-spin" />,
    cls: "text-blue-600",
  },
  passed: { label: "通过", icon: <CheckCircle size={14} />, cls: "text-brand-700" },
  failed: { label: "失败", icon: <XCircle size={14} />, cls: "text-red-600" },
  healing: { label: "自愈中", icon: <Wrench size={14} />, cls: "text-amber-600" },
};

function StatusCell({ status }: { status: CaseRunStatus }) {
  const m = STATUS_META[status] ?? STATUS_META.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm ${m.cls}`}>
      {m.icon}
      {m.label}
    </span>
  );
}

export default function SuiteCasesPage() {
  const { id } = useParams<{ id: string }>();
  const [suite, setSuite] = useState<SuiteResp | null>(null);
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Case | null>(null);
  // 最近一次历史 run 的逐用例裁决(caseId → passed),供未实时执行时回填状态列
  const [pastStatus, setPastStatus] = useState<Record<string, CaseRunStatus>>({});

  const run = useSuiteRun(id);

  async function load() {
    setSuite(await apiGet(`/suites/${id}`));
  }
  useEffect(() => {
    load();
  }, [id]);

  // 拉最近一次 run 的逐用例结果,使列表状态与抽屉(同一 run)保持一致
  useEffect(() => {
    const runs = (suite?.runs ?? []).slice().sort((a, b) => b.started_at - a.started_at);
    const latest = runs[0]?.id;
    if (!latest) {
      setPastStatus({});
      return;
    }
    apiGet<RunOverview>(`/suites/${id}/runs/${latest}`)
      .then((ov) => {
        const m: Record<string, CaseRunStatus> = {};
        for (const c of ov.cases) m[c.case_id] = c.passed ? "passed" : "failed";
        setPastStatus(m);
      })
      .catch(() => setPastStatus({}));
  }, [id, suite?.runs]);
  useEffect(() => () => run.stop(), []); // cleanup SSE on unmount

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      await apiPost(`/suites/${id}/upload`, fd);
      await load();
    } catch (err) {
      alert("上传失败: " + (err instanceof Error ? err.message : String(err)));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  const cases = suite?.cases ?? [];
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return cases;
    return cases.filter(
      (c) => c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q),
    );
  }, [cases, query]);

  function statusOf(caseId: string): CaseRunStatus {
    // 本次会话的实时状态优先;否则回退到最近一次历史 run 的裁决
    return run.statuses[caseId]?.status ?? pastStatus[caseId] ?? "pending";
  }

  // 进度统计
  const tracked = Object.values(run.statuses);
  const completed = tracked.filter(
    (c) => c.status === "passed" || c.status === "failed",
  ).length;
  const activeCount = tracked.filter((c) => c.status === "running").length;
  const showProgress = run.running || run.done;

  function startRun() {
    setSelected(null);
    run.start(cases.map((c) => c.id));
  }

  // 抽屉数据源:本次会话刚跑的 run 优先,否则取套件最近一次 run
  const selRun = selected ? run.statuses[selected.id] : undefined;
  const latestPastRun =
    (suite?.runs ?? []).slice().sort((a, b) => b.started_at - a.started_at)[0]
      ?.id ?? null;
  const effectiveRunId = run.runId ?? latestPastRun;

  return (
    <div>
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-surface-900">用例</h1>
          <p className="text-sm text-gray-500 mt-1">
            {suite?.base_url || "未设置 Base URL"}
          </p>
        </div>
        <div className="flex gap-2">
          <label
            className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-md text-sm font-medium border transition-colors cursor-pointer ${
              uploading
                ? "border-gray-200 text-gray-400 cursor-not-allowed"
                : "border-gray-300 text-gray-700 hover:bg-gray-50"
            }`}
          >
            {uploading ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Upload size={16} />
            )}
            {uploading ? "解析中…" : "上传 Excel"}
            <input
              type="file"
              accept=".xlsx,.xls"
              className="hidden"
              onChange={handleUpload}
              disabled={uploading}
            />
          </label>
          <button
            onClick={startRun}
            disabled={cases.length === 0 || run.running}
            className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-3.5 py-2 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {run.running ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Play size={16} />
            )}
            {run.running ? "执行中…" : "执行"}
          </button>
        </div>
      </div>

      {/* Progress bar (during/after run) */}
      {showProgress && (
        <div className="mb-5 p-4 bg-white border border-gray-200 rounded-lg">
          <div className="flex items-center justify-between mb-2 text-sm">
            <span className="text-surface-900 font-medium">
              {run.done
                ? "执行完成"
                : `${completed} / ${cases.length} 完成`}
              {!run.done && activeCount > 0 && (
                <span className="text-gray-500 font-normal">
                  {" "}
                  · {activeCount} 个执行中
                </span>
              )}
            </span>
            {run.result && (
              <span className="text-gray-500">
                <span className="text-brand-700">{run.result.passed} 通过</span>
                {run.result.failed > 0 && (
                  <span className="text-red-600"> · {run.result.failed} 失败</span>
                )}
              </span>
            )}
          </div>
          <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${
                run.done ? "bg-brand-600" : "bg-blue-500"
              }`}
              style={{
                width: `${cases.length ? (completed / cases.length) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      )}

      {run.error && (
        <div className="mb-5 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {run.error}
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
            placeholder="搜索用例…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <span className="text-xs text-gray-400 ml-auto">共 {cases.length} 条用例</span>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500">
              <th className="px-5 py-3 font-medium w-28">状态</th>
              <th className="px-5 py-3 font-medium">名称</th>
              <th className="px-5 py-3 font-medium w-32">ID</th>
              <th className="px-5 py-3 font-medium w-20">步骤数</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={4} className="px-5 py-16 text-center">
                  <FileSpreadsheet
                    size={36}
                    className="mx-auto text-gray-300 mb-3"
                  />
                  <p className="text-gray-500 text-sm mb-1">
                    {query ? "没有匹配的用例" : "尚未上传用例"}
                  </p>
                  {!query && (
                    <p className="text-xs text-gray-400">
                      上传 Excel 文件以导入测试用例
                    </p>
                  )}
                </td>
              </tr>
            )}
            {filtered.map((c) => (
              <tr
                key={c.id}
                onClick={() => setSelected(c)}
                className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70 cursor-pointer transition-colors"
              >
                <td className="px-5 py-3.5">
                  <StatusCell status={statusOf(c.id)} />
                </td>
                <td className="px-5 py-3.5 font-medium text-surface-900">
                  {c.name}
                </td>
                <td className="px-5 py-3.5 font-mono text-xs text-gray-500">
                  {c.id}
                </td>
                <td className="px-5 py-3.5 text-gray-500">{c.steps.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Case drawer */}
      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        width="max-w-5xl"
        title={
          selected && (
            <p className="text-sm text-gray-500 truncate">{selected.name}</p>
          )
        }
      >
        {selected && (
          <CaseDrawerBody
            suiteId={id!}
            runId={effectiveRunId}
            caseInfo={selected}
            status={statusOf(selected.id)}
            liveState={selRun}
          />
        )}
      </Drawer>

      {/* Permission dialog during run */}
      {run.permission && (
        <PermissionDialog
          eventId={run.permission.event_id}
          caseId={run.permission.case_id}
          action={run.permission.action}
          reason={run.permission.reason}
          suiteId={id!}
          onResolved={run.clearPermission}
        />
      )}
    </div>
  );
}
