import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet } from "../api/client";

interface RunDetail {
  id: string;
  suite_id: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  started_at: number;
  finished_at: number | null;
}

interface CaseResult {
  case_id: string;
  passed: boolean;
  final_result: string;
}

export default function RunOverviewPage() {
  const { id, runId } = useParams<{ id: string; runId: string }>();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [cases, setCases] = useState<CaseResult[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id || !runId) return;
    Promise.all([
      apiGet<RunDetail>(`/suites/${id}/runs/${runId}`).catch((e) => { setError(e.message); return null; }),
    ]).then(([r]) => { if (r) setRun(r); });
    apiGet<CaseResult[]>(`/suites/${id}/runs/${runId}/cases`).catch(() => {})
      .then((c) => { if (c) setCases(c); });
  }, [id, runId]);

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}`)} className="text-sm text-gray-500 hover:underline mb-2">
        ← 返回 Suite
      </button>

      {error && <p className="text-red-600 text-sm mb-2">{error}</p>}

      {run && (
        <div className="mb-6">
          <h2 className="text-xl font-bold mb-1">执行详情</h2>
          <p className="text-sm text-gray-500">
            状态: <span className={run.status === "completed" ? "text-green-600" : run.status === "failed" ? "text-red-600" : "text-cyan-600"}>{run.status}</span>
            {" · "}
            {run.passed_cases} 通过 / {run.failed_cases} 失败 / 共 {run.total_cases}
          </p>
          {run.started_at && (
            <p className="text-xs text-gray-400">
              开始: {new Date(run.started_at * 1000).toLocaleString()}
              {run.finished_at && ` · 结束: ${new Date(run.finished_at * 1000).toLocaleString()}`}
            </p>
          )}
        </div>
      )}

      <div className="bg-white border rounded overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="text-left px-4 py-2">用例</th>
              <th className="text-left px-4 py-2">结果</th>
              <th className="text-left px-4 py-2">详情</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.case_id} className="border-t hover:bg-gray-50 cursor-pointer"
                onClick={() => navigate(`/suites/${id}/runs/${runId}/case/${c.case_id}`)}>
                <td className="px-4 py-2 font-mono text-xs">{c.case_id}</td>
                <td className="px-4 py-2">
                  <span className={c.passed ? "text-green-600" : "text-red-600"}>
                    {c.passed ? "PASS" : "FAIL"}
                  </span>
                </td>
                <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-xs">{c.final_result}</td>
              </tr>
            ))}
            {cases.length === 0 && (
              <tr><td colSpan={3} className="px-4 py-8 text-center text-gray-400">暂无用例数据</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}