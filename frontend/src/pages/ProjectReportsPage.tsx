// 平台化 M2:项目报告页 —— 版本维度 run 汇总 + 审计日志。
import { useEffect, useState } from "react";
import { apiGet } from "../api/client";
import { getProjectId } from "../lib/session";

interface Run {
  id: string;
  version_id: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
}
interface RunsResp {
  runs: Run[];
  summary: { run_count: number; passed: number; failed: number };
}
interface Audit {
  actor: string;
  action: string;
  target: string;
  detail: string;
  created_at: number;
}

export default function ProjectReportsPage() {
  const pid = getProjectId();
  const [version, setVersion] = useState("");
  const [runs, setRuns] = useState<RunsResp | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);

  useEffect(() => {
    if (!pid) return;
    apiGet<RunsResp>(`/projects/${pid}/runs${version ? `?version_id=${version}` : ""}`)
      .then(setRuns)
      .catch(() => {});
    apiGet<Audit[]>(`/projects/${pid}/audit`).then(setAudit).catch(() => {});
  }, [pid, version]);

  if (!pid) return <div className="text-sm text-gray-500">请先选择项目。</div>;

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-surface-900">项目报告</h1>
        <p className="text-sm text-gray-500 mt-1">版本维度执行汇总与审计日志。</p>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-surface-900">执行汇总</h3>
          <input
            placeholder="按版本过滤(version_id)"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            className="border border-gray-300 rounded-md px-2 py-1 text-xs"
          />
        </div>
        {runs && (
          <p className="text-sm text-gray-600 mb-3">
            共 {runs.summary.run_count} 次执行 · 通过用例 {runs.summary.passed} · 失败{" "}
            {runs.summary.failed}
          </p>
        )}
        <div className="divide-y divide-gray-100 border border-gray-100 rounded-md text-sm">
          {(runs?.runs ?? []).map((r) => (
            <div key={r.id} className="flex items-center justify-between px-3 py-2">
              <span className="text-surface-900">
                {r.id.slice(0, 8)} · {r.version_id || "—"}
              </span>
              <span className="text-gray-500">
                {r.status} · {r.passed_cases}/{r.total_cases} 通过
              </span>
            </div>
          ))}
          {runs && runs.runs.length === 0 && (
            <p className="text-xs text-gray-400 px-3 py-2">暂无执行</p>
          )}
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-5">
        <h3 className="text-sm font-medium text-surface-900 mb-3">审计日志</h3>
        <div className="divide-y divide-gray-100 text-sm">
          {audit.map((a, i) => (
            <div key={i} className="flex items-center gap-3 px-1 py-2">
              <span className="text-gray-400 text-xs w-32 shrink-0">
                {new Date(a.created_at * 1000).toLocaleString()}
              </span>
              <span className="text-brand-700">{a.action}</span>
              <span className="text-gray-500 truncate">
                {a.actor} {a.target} {a.detail}
              </span>
            </div>
          ))}
          {audit.length === 0 && <p className="text-xs text-gray-400 py-2">暂无</p>}
        </div>
      </div>
    </div>
  );
}
