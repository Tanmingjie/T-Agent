// 测试任务报告(任务级):该任务历次执行的汇总。当前为基础版,后续补充
// (趋势 / 通过率曲线 / 断言维度等)。run 列表见「执行历史」,此处偏汇总指标。
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet } from "../api/client";

interface Run {
  id: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  started_at?: number;
}
interface SuiteResp {
  name: string;
  runs: Run[];
}

export default function SuiteReportsPage() {
  const { id } = useParams<{ id: string }>();
  const [runs, setRuns] = useState<Run[]>([]);

  useEffect(() => {
    apiGet<SuiteResp>(`/suites/${id}`)
      .then((s) => setRuns(s.runs ?? []))
      .catch(() => {});
  }, [id]);

  const stat = useMemo(() => {
    const runCount = runs.length;
    const passed = runs.reduce((a, r) => a + r.passed_cases, 0);
    const failed = runs.reduce((a, r) => a + r.failed_cases, 0);
    const last = runs[0]; // /suites 返回按时间倒序
    const lastRate =
      last && last.total_cases > 0
        ? Math.round((last.passed_cases / last.total_cases) * 100)
        : null;
    return { runCount, passed, failed, lastRate };
  }, [runs]);

  return (
    <div className="max-w-3xl">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">报告</h1>
        <p className="text-sm text-gray-500 mt-1">
          该测试任务的执行汇总。更详细的报告维度后续补充。
        </p>
      </div>

      <div className="grid grid-cols-4 gap-4 mb-6">
        <Stat label="执行次数" value={stat.runCount} />
        <Stat
          label="最近通过率"
          value={stat.lastRate === null ? "—" : `${stat.lastRate}%`}
          tone={stat.lastRate === null ? undefined : stat.lastRate === 100 ? "brand" : "red"}
        />
        <Stat label="累计通过用例" value={stat.passed} tone="brand" />
        <Stat label="累计失败用例" value={stat.failed} tone={stat.failed > 0 ? "red" : undefined} />
      </div>

      {runs.length === 0 && (
        <div className="bg-white border border-gray-200 rounded-lg px-5 py-12 text-center text-sm text-gray-400">
          暂无执行记录，跑一次后这里会出现汇总。
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
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
