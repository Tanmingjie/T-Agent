// 项目概览(默认落脚页)。项目级汇总:版本数 / LLM 配置状态 / 最近跨版本执行。
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Layers, Cpu, Activity, ChevronRight } from "lucide-react";
import { withProject } from "../lib/session";
import { apiGet } from "../api/client";
import { getProjectId } from "../lib/session";

interface Version {
  id: string;
  name: string;
}
interface Run {
  id: string;
  suite_id: string;
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
interface LLMConfig {
  model: string;
  has_key: boolean;
}

function statusColor(s: string): string {
  if (s === "completed") return "text-brand-700";
  if (s === "running") return "text-blue-600";
  if (s === "failed" || s === "error") return "text-red-600";
  return "text-gray-500";
}

export default function ProjectOverviewPage() {
  const pid = getProjectId();
  const [versions, setVersions] = useState<Version[]>([]);
  const [taskCount, setTaskCount] = useState(0);
  const [runs, setRuns] = useState<RunsResp | null>(null);
  const [llm, setLlm] = useState<LLMConfig | null>(null);

  useEffect(() => {
    if (!pid) return;
    apiGet<Version[]>(`/projects/${pid}/versions`).then(setVersions).catch(() => {});
    apiGet<{ id: string }[]>(withProject("/suites"))
      .then((s) => setTaskCount(s.length))
      .catch(() => {});
    apiGet<RunsResp>(`/projects/${pid}/runs`).then(setRuns).catch(() => {});
    apiGet<LLMConfig>(`/projects/${pid}/llm-config`).then(setLlm).catch(() => {});
  }, [pid]);

  if (!pid) {
    return (
      <div className="max-w-md mt-10 text-center">
        <h1 className="text-lg font-semibold text-surface-900 mb-2">未指定项目</h1>
        <p className="text-sm text-gray-500">
          请通过内网系统选择项目后进入,或在 URL 加 <code>?project=&lt;id&gt;</code>。
        </p>
      </div>
    );
  }

  const vName = (id: string) => versions.find((v) => v.id === id)?.name || id || "—";

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">概览</h1>
        <p className="text-sm text-gray-500 mt-1">项目整体状态与最近执行。</p>
      </div>

      {/* 状态卡 */}
      <div className="grid sm:grid-cols-3 gap-4 mb-8">
        <Link
          to="/tasks"
          className="bg-white border border-gray-200 rounded-lg p-5 border-l-4 border-l-brand-500 hover:shadow-card transition-shadow"
        >
          <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
            <Layers size={14} /> 测试任务
          </div>
          <p className="text-2xl font-semibold text-surface-900">{taskCount}</p>
          <p className="text-xs text-gray-400 mt-1">跨 {versions.length} 个版本</p>
        </Link>

        <Link
          to="/settings"
          className="bg-white border border-gray-200 rounded-lg p-5 border-l-4 border-l-brand-500 hover:shadow-card transition-shadow"
        >
          <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
            <Cpu size={14} /> LLM
          </div>
          {llm?.has_key ? (
            <p className="text-sm font-medium text-brand-700">已配置 · {llm.model}</p>
          ) : (
            <p className="text-sm font-medium text-amber-600">未配置</p>
          )}
        </Link>

        <div className="bg-white border border-gray-200 rounded-lg p-5 border-l-4 border-l-brand-500">
          <div className="flex items-center gap-2 text-gray-500 text-xs mb-2">
            <Activity size={14} /> 执行
          </div>
          <p className="text-2xl font-semibold text-surface-900">
            {runs?.summary.run_count ?? 0}
            <span className="text-sm font-normal text-gray-400 ml-2">次</span>
          </p>
        </div>
      </div>

      {/* 最近执行 */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="px-5 py-3 border-b border-gray-100">
          <h3 className="text-sm font-medium text-surface-900">最近执行</h3>
        </div>
        <div className="divide-y divide-gray-100">
          {(runs?.runs ?? []).slice(0, 12).map((r) => (
            <Link
              key={r.id}
              to={`/suites/${r.suite_id}/runs/${r.id}`}
              className="flex items-center justify-between px-5 py-3 text-sm hover:bg-gray-50/70 transition-colors group"
            >
              <span className="flex items-center gap-2">
                <span className="text-gray-400 font-mono text-xs">{r.id.slice(0, 8)}</span>
                <span className="text-surface-900">{vName(r.version_id)}</span>
              </span>
              <span className="flex items-center gap-3">
                <span className="text-gray-500">
                  {r.passed_cases}/{r.total_cases} 通过
                </span>
                <span className={statusColor(r.status)}>{r.status}</span>
                <ChevronRight size={15} className="text-gray-300 group-hover:text-brand-600" />
              </span>
            </Link>
          ))}
          {runs && runs.runs.length === 0 && (
            <p className="px-5 py-10 text-center text-sm text-gray-400">暂无执行记录</p>
          )}
        </div>
      </div>
    </div>
  );
}
