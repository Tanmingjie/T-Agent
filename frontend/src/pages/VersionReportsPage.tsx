// 版本报告(版本级页面):当前版本跨套件的执行汇总。版本由 VersionLayout 经 outlet 传入。
import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { apiGet } from "../api/client";
import { getProjectId } from "../lib/session";

interface Run {
  id: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
}
interface RunsResp {
  runs: Run[];
  summary: { run_count: number; passed: number; failed: number };
}

function statusColor(s: string): string {
  if (s === "completed") return "text-brand-700";
  if (s === "running") return "text-blue-600";
  if (s === "failed" || s === "error") return "text-red-600";
  return "text-gray-500";
}

export default function VersionReportsPage() {
  const { versionId } = useOutletContext<{ versionId: string }>();
  const pid = getProjectId();
  const [runs, setRuns] = useState<RunsResp | null>(null);

  useEffect(() => {
    if (!pid || !versionId) return;
    apiGet<RunsResp>(`/projects/${pid}/runs?version_id=${encodeURIComponent(versionId)}`)
      .then(setRuns)
      .catch(() => {});
  }, [pid, versionId]);

  return (
    <div className="max-w-3xl">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">报告</h1>
        <p className="text-sm text-gray-500 mt-1">当前版本各套件的执行汇总。</p>
      </div>

      {runs && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <Stat label="执行次数" value={runs.summary.run_count} />
          <Stat label="通过用例" value={runs.summary.passed} tone="brand" />
          <Stat label="失败用例" value={runs.summary.failed} tone="red" />
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-100">
          <h3 className="text-sm font-medium text-surface-900">执行记录</h3>
        </div>
        <div className="divide-y divide-gray-100 text-sm">
          {(runs?.runs ?? []).map((r) => (
            <div key={r.id} className="flex items-center justify-between px-5 py-3">
              <span className="font-mono text-xs text-gray-400">{r.id.slice(0, 8)}</span>
              <span className="flex items-center gap-3">
                <span className="text-gray-500">
                  {r.passed_cases}/{r.total_cases} 通过
                </span>
                <span className={statusColor(r.status)}>{r.status}</span>
              </span>
            </div>
          ))}
          {runs && runs.runs.length === 0 && (
            <p className="px-5 py-10 text-center text-gray-400">该版本暂无执行记录</p>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "brand" | "red";
}) {
  const color =
    tone === "brand" ? "text-brand-700" : tone === "red" ? "text-red-600" : "text-surface-900";
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-2xl font-semibold ${color}`}>{value}</p>
    </div>
  );
}
