import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet } from "../api/client";
import StepListPanel from "../components/StepListPanel";

interface StepDetail {
  step_no: number;
  model_output: { reasoning: string; tool_name: string; tool_input: Record<string, unknown> };
  action_result: { tool_result: string; url: string; screenshot: string | null; duration_ms: number };
}

interface CaseResult {
  case_id: string;
  passed: boolean;
  final_result: string;
  token_usage: number;
  heal_count: number;
  history: StepDetail[];
  case_assertions: { type: string; status: string; target: string; reason?: string }[];
}

export default function CaseResultPage() {
  const { id, runId, caseId } = useParams<{ id: string; runId: string; caseId: string }>();
  const navigate = useNavigate();
  const [result, setResult] = useState<CaseResult | null>(null);
  const [selectedStep, setSelectedStep] = useState<number | null>(null);
  const [tab, setTab] = useState<"snapshot" | "code" | "log">("snapshot");

  useEffect(() => {
    if (caseId) {
      apiGet<CaseResult>(`/suites/${id}/runs/${runId}/cases/${caseId}/result`).then(setResult);
    }
  }, [id, runId, caseId]);

  if (!result) return <p>加载中...</p>;

  const selected = result.history.find((s) => s.step_no === selectedStep);

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}/runs/${runId}`)} className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-surface-900 mb-3 transition-colors">
        ← 返回执行
      </button>

      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-surface-900">{caseId}</h1>
        <div className="flex items-center gap-2">
          <span className={`px-2.5 py-1 rounded-md text-xs font-medium border ${result.passed ? "bg-brand-50 text-brand-700 border-brand-200" : "bg-red-50 text-red-700 border-red-200"}`}>
            {result.passed ? "PASS" : "FAIL"}
          </span>
          <button onClick={() => navigate(`/suites/${id}/runs/${runId}/case/${caseId}/code`)}
            className="border border-gray-300 text-gray-700 px-3.5 py-1.5 rounded-md text-sm font-medium hover:bg-gray-50 transition-colors">
            查看代码
          </button>
        </div>
      </div>

      <p className="text-sm text-gray-500 mb-4">
        Token: {result.token_usage} · 自愈: {result.heal_count} 次
      </p>

      <div className="grid grid-cols-2 gap-6">
        {/* Left: Step list */}
        <div className="bg-white border rounded p-4">
          <StepListPanel
            steps={result.history.map((s) => ({
              step_no: s.step_no,
              tool_name: s.model_output.tool_name,
              reasoning: s.model_output.reasoning,
              screenshot: s.action_result.screenshot,
              assertion_results: [],
            }))}
            onSelect={setSelectedStep}
            selected={selectedStep}
          />

          {result.case_assertions.length > 0 && (
            <div className="mt-4 pt-4 border-t">
              <h4 className="font-semibold text-sm mb-2">最终断言</h4>
              {result.case_assertions.map((a, i) => (
                <div key={i} className={`text-xs py-1 ${a.status === "pass" ? "text-green-600" : "text-red-600"}`}>
                  {a.status === "pass" ? "✓" : "✗"} [{a.type}] {a.target} {a.reason || ""}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Detail panel with tabs */}
        <div className="bg-white border rounded p-4">
          <div className="flex gap-2 mb-4 border-b pb-2">
            {(["snapshot", "code", "log"] as const).map((t) => (
              <button key={t}
                onClick={() => setTab(t)}
                className={`px-3 py-1 text-sm rounded-md ${tab === t ? "bg-brand-50 text-brand-700" : "hover:bg-gray-50 text-gray-600"}`}
              >
                {t === "snapshot" ? "快照" : t === "code" ? "代码" : "日志"}
              </button>
            ))}
          </div>

          {!selected ? (
            <p className="text-gray-400 text-sm">点击左侧步骤查看详情</p>
          ) : tab === "snapshot" ? (
            selected.action_result.screenshot ? (
              <img
                src={`/api/screenshots/${runId}/${caseId}/step_${String(selected.step_no).padStart(3, "0")}.png`}
                alt={`Step ${selected.step_no} screenshot`}
                className="max-w-full rounded border"
              />
            ) : (
              <p className="text-gray-400 text-sm">该步骤无截图</p>
            )
          ) : tab === "code" ? (
            <pre className="text-xs bg-gray-900 text-gray-100 p-3 rounded overflow-auto max-h-96">
              <code>{selected.model_output.tool_name}({JSON.stringify(selected.model_output.tool_input, null, 2)})</code>
            </pre>
          ) : (
            <div className="text-xs">
              <p className="text-gray-500 mb-2">推理:</p>
              <pre className="whitespace-pre-wrap bg-gray-50 p-2 rounded mb-4">{selected.model_output.reasoning}</pre>
              <p className="text-gray-500 mb-2">工具结果:</p>
              <pre className="whitespace-pre-wrap bg-gray-50 p-2 rounded">{selected.action_result.tool_result}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
