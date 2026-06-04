import { useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { sseUrl, apiPost, safeParse } from "../api/client";
import ProgressBar from "../components/ProgressBar";
import StatusBadge from "../components/StatusBadge";
import PermissionDialog from "../components/PermissionDialog";
import { Play, CheckCircle, XCircle, Clock, Loader2, ArrowLeft, BarChart3, Activity } from "lucide-react";

interface CaseStatus {
  case_id: string;
  title: string;
  status: "pending" | "running" | "passed" | "failed" | "healing";
  steps: StepStatus[];
}

interface StepStatus {
  index: number;
  status: string;
  description: string;
}

interface PermReq {
  event_id: string;
  case_id: string;
  action: string;
  reason: string;
}

export default function RunConsolePage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [statuses, setStatuses] = useState<CaseStatus[]>([]);
  const [currentCase, setCurrentCase] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [result, setResult] = useState<{ passed: number; failed: number; total: number } | null>(null);
  const [permission, setPermission] = useState<PermReq | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  async function start() {
    try {
      const { run_id } = await apiPost<{ run_id: string }>(`/suites/${id}/run`);
      setRunId(run_id);
      setError(null);

      const url = sseUrl(`/suites/${id}/stream?run_id=${run_id}`);
      const es = new EventSource(url);
      esRef.current = es;

      es.addEventListener("suite_start", (e) => {
        const d = safeParse(e.data);
        if (!d) return;
        setStatuses([]);
        setDone(false);
        setResult(null);
      });

      es.addEventListener("case_start", (e) => {
        const d = safeParse(e.data);
        if (!d) return;
        setStatuses((prev) => [
          ...prev,
          { case_id: d.case_id as string, title: d.title as string, status: "running", steps: [] },
        ]);
        setCurrentCase(d.case_id as string);
      });

      es.addEventListener("step_change", (e) => {
        const d = safeParse(e.data);
        if (!d) return;
        setStatuses((prev) =>
          prev.map((c) =>
            c.case_id === d.case_id
              ? {
                  ...c,
                  steps: [
                    ...c.steps.filter((s) => s.index !== d.step_index),
                    { index: d.step_index as number, status: d.status as string, description: d.description as string },
                  ],
                }
              : c
          )
        );
      });

      es.addEventListener("step_done", (e) => {
        const d = safeParse(e.data);
        if (!d) return;
        setStatuses((prev) =>
          prev.map((c) =>
            c.case_id === d.case_id
              ? {
                  ...c,
                  steps: c.steps.map((s) =>
                    s.index === d.step_index ? { ...s, status: "done" } : s
                  ),
                }
              : c
          )
        );
      });

      es.addEventListener("case_result", (e) => {
        const d = safeParse(e.data);
        if (!d) return;
        setStatuses((prev) =>
          prev.map((c) =>
            c.case_id === d.case_id
              ? { ...c, status: d.verdict === "PASS" ? "passed" : "failed" }
              : c
          )
        );
      });

      es.addEventListener("permission", (e) => {
        const d = safeParse(e.data);
        if (d) setPermission(d as unknown as PermReq);
      });

      es.addEventListener("suite_done", (e) => {
        const d = safeParse(e.data);
        if (d) {
          setDone(true);
          setResult({ passed: d.passed as number, failed: d.failed as number, total: d.total as number });
        }
        es.close();
      });

      es.addEventListener("error", () => {
        // EventSource auto-reconnects; we just note it
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    start();
    return () => esRef.current?.close();
  }, [id]);

  const completed = statuses.filter((c) => c.status === "passed" || c.status === "failed").length;
  const active = statuses.find((c) => c.status === "running");

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}`)} className="text-sm text-gray-500 hover:underline mb-2">
        ← 返回 Suite
      </button>

      {error && <p className="text-red-600 text-sm mb-4">{error}</p>}

      <h2 className="text-xl font-bold mb-1">执行控制台</h2>
      <p className="text-sm text-gray-500 mb-4">
        {done ? "✅ 执行完成" : "⏳ 运行中"} · {completed}/{statuses.length} 完成
      </p>

      <ProgressBar value={completed} max={statuses.length} />

      <div className="grid grid-cols-2 gap-6">
        {/* Left: Case list */}
        <div className="bg-white border rounded p-4">
          <h3 className="font-semibold mb-3">用例列表</h3>
          {statuses.map((c) => (
            <div
              key={c.case_id}
              className={`flex items-center gap-2 py-2 border-b last:border-0 text-sm ${
                c.case_id === currentCase ? "bg-cyan-50 -mx-2 px-2 rounded" : ""
              }`}
            >
              <span>
                {c.status === "running" ? "▶" :
                 c.status === "passed" ? "✅" :
                 c.status === "failed" ? "❌" :
                 c.status === "healing" ? "🟡" : "⏳"}
              </span>
              <span>{c.title}</span>
            </div>
          ))}
        </div>

        {/* Right: Detail */}
        <div className="bg-white border rounded p-4">
          <h3 className="font-semibold mb-3">当前步骤</h3>
          {active ? (
            <div>
              <p className="text-sm text-gray-600 mb-2">
                ▶ {active.title}
              </p>
              {active.steps
                .sort((a, b) => a.index - b.index)
                .map((s) => (
                  <div key={s.index} className="text-xs py-1 flex items-center gap-2">
                    <span>{s.status === "done" ? "✅" : "▶"}</span>
                    <span>Step {s.index + 1}: {s.description}</span>
                  </div>
                ))}
            </div>
          ) : (
            <p className="text-gray-400 text-sm">
              {done ? "所有用例执行完毕。" : "等待执行开始..."}
            </p>
          )}
        </div>
      </div>

      {done && result && (
        <div className="mt-6 bg-white border rounded p-4">
          <h3 className="font-semibold mb-2">执行结果</h3>
          <p>✅ {result.passed} 通过 · ❌ {result.failed} 失败 · 共 {result.total}</p>
          <button
            onClick={() => navigate(`/suites/${id}/runs/${runId}`)}
            className="mt-2 bg-cyan-600 text-white px-4 py-1 rounded text-sm"
          >
            查看详情
          </button>
        </div>
      )}

      {permission && (
        <PermissionDialog
          eventId={permission.event_id}
          caseId={permission.case_id}
          action={permission.action}
          reason={permission.reason}
          suiteId={id!}
          onResolved={() => setPermission(null)}
        />
      )}
    </div>
  );
}