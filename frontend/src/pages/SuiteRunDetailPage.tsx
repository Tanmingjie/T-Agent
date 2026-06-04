import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import Drawer from "../components/Drawer";
import CaseDrawerBody from "../components/CaseDrawerBody";
import { CheckCircle, XCircle, ChevronLeft } from "lucide-react";

interface Case {
  id: string;
  name: string;
  steps: string[];
  preconditions: string[];
  expected: string[];
}

interface RunCase {
  case_id: string;
  passed: boolean;
  steps_count: number;
  token_usage: number;
}

interface RunDetail {
  id: string;
  status: string;
  total_cases: number;
  passed_cases: number;
  failed_cases: number;
  started_at: number;
  cases: RunCase[];
}

type BadgeStatus = "passed" | "failed" | "running" | "completed" | "aborted";

export default function SuiteRunDetailPage() {
  const { id, runId } = useParams<{ id: string; runId: string }>();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [caseMap, setCaseMap] = useState<Record<string, Case>>({});
  const [selected, setSelected] = useState<{ info: Case; passed: boolean } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id || !runId) return;
    apiGet<RunDetail>(`/suites/${id}/runs/${runId}`)
      .then(setRun)
      .catch((e) => setError(e.message));
    apiGet<{ cases: Case[] }>(`/suites/${id}`)
      .then((s) => {
        const m: Record<string, Case> = {};
        for (const c of s.cases) m[c.id] = c;
        setCaseMap(m);
      })
      .catch(() => {});
  }, [id, runId]);

  function openCase(rc: RunCase) {
    const info =
      caseMap[rc.case_id] ??
      ({
        id: rc.case_id,
        name: rc.case_id,
        steps: [],
        preconditions: [],
        expected: [],
      } as Case);
    setSelected({ info, passed: rc.passed });
  }

  const cases = run?.cases ?? [];

  return (
    <div>
      <button
        onClick={() => navigate(`/suites/${id}/history`)}
        className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-surface-900 mb-3 transition-colors"
      >
        <ChevronLeft size={16} /> 执行历史
      </button>

      {error && (
        <div className="mb-5 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {error}
        </div>
      )}

      {run && (
        <div className="mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-surface-900">执行详情</h1>
            <StatusBadge status={(run.status as BadgeStatus) ?? "pending"} />
          </div>
          <p className="text-sm text-gray-500 mt-1">
            <span className="text-brand-700 font-medium">{run.passed_cases} 通过</span>
            {run.failed_cases > 0 && (
              <span className="text-red-600"> · {run.failed_cases} 失败</span>
            )}
            <span className="text-gray-400"> · 共 {run.total_cases}</span>
            {run.started_at > 0 && (
              <span className="text-gray-400">
                {" · "}
                {new Date(run.started_at * 1000).toLocaleString("zh-CN")}
              </span>
            )}
          </p>
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500">
              <th className="px-5 py-3 font-medium w-24">结果</th>
              <th className="px-5 py-3 font-medium">用例</th>
              <th className="px-5 py-3 font-medium w-24">步骤数</th>
              <th className="px-5 py-3 font-medium w-28">Token</th>
            </tr>
          </thead>
          <tbody>
            {cases.length === 0 && (
              <tr>
                <td colSpan={4} className="px-5 py-16 text-center text-gray-400">
                  暂无用例数据
                </td>
              </tr>
            )}
            {cases.map((c) => (
              <tr
                key={c.case_id}
                onClick={() => openCase(c)}
                className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70 cursor-pointer transition-colors"
              >
                <td className="px-5 py-3.5">
                  <span
                    className={`inline-flex items-center gap-1.5 text-sm ${
                      c.passed ? "text-brand-700" : "text-red-600"
                    }`}
                  >
                    {c.passed ? <CheckCircle size={15} /> : <XCircle size={15} />}
                    {c.passed ? "通过" : "失败"}
                  </span>
                </td>
                <td className="px-5 py-3.5 text-surface-900">
                  {caseMap[c.case_id]?.name ?? (
                    <span className="font-mono text-xs text-gray-600">{c.case_id}</span>
                  )}
                </td>
                <td className="px-5 py-3.5 text-gray-500">{c.steps_count}</td>
                <td className="px-5 py-3.5 text-gray-500">{c.token_usage}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Case drawer (复用用例表的双栏抽屉,绑定本次 run) */}
      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        width="max-w-5xl"
        title={
          selected && (
            <p className="text-sm text-gray-500 truncate">{selected.info.name}</p>
          )
        }
      >
        {selected && (
          <CaseDrawerBody
            suiteId={id!}
            runId={runId!}
            caseInfo={selected.info}
            status={selected.passed ? "passed" : "failed"}
          />
        )}
      </Drawer>
    </div>
  );
}
