import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import { History } from "lucide-react";

interface Run {
  id: string;
  status: string;
  passed_cases: number;
  failed_cases: number;
  total_cases: number;
  started_at: number;
}

interface SuiteResp {
  name: string;
  runs: Run[];
}

function fmtTs(ts: number): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type BadgeStatus = "passed" | "failed" | "running" | "completed" | "aborted";

export default function SuiteHistoryPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<Run[]>([]);

  useEffect(() => {
    apiGet<SuiteResp>(`/suites/${id}`)
      .then((s) => setRuns(s.runs ?? []))
      .catch(() => {});
  }, [id]);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">执行历史</h1>
        <p className="text-sm text-gray-500 mt-1">查看该测试任务的历次执行记录与结果。</p>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500">
              <th className="px-5 py-3 font-medium">状态</th>
              <th className="px-5 py-3 font-medium">结果</th>
              <th className="px-5 py-3 font-medium">开始时间</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={3} className="px-5 py-16 text-center">
                  <History size={36} className="mx-auto text-gray-300 mb-3" />
                  <p className="text-gray-500 text-sm">暂无执行记录</p>
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <tr
                key={r.id}
                onClick={() => navigate(`/suites/${id}/runs/${r.id}`)}
                className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70 cursor-pointer transition-colors"
              >
                <td className="px-5 py-3.5">
                  <StatusBadge status={(r.status as BadgeStatus) ?? "pending"} />
                </td>
                <td className="px-5 py-3.5 text-gray-600">
                  <span className="text-brand-700 font-medium">{r.passed_cases}</span>
                  <span className="text-gray-400"> / {r.total_cases} 通过</span>
                  {r.failed_cases > 0 && (
                    <span className="text-red-600"> · {r.failed_cases} 失败</span>
                  )}
                </td>
                <td className="px-5 py-3.5 text-gray-500">{fmtTs(r.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
