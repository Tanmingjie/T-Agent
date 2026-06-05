import { useCallback, useRef, useState } from "react";
import { sseUrl, apiPost, safeParse } from "../api/client";

export type CaseRunStatus =
  | "pending"
  | "running"
  | "passed"
  | "failed"
  | "healing";

export interface StepStatus {
  index: number;
  status: string;
  description: string;
}

export interface PhaseStatus {
  phase: string;
  label: string;
}

export interface CaseRunState {
  status: CaseRunStatus;
  steps: StepStatus[];
  phases: PhaseStatus[]; // 生命周期阶段流(翻译/执行/断言/代码),最后一个为当前进行中
}

export interface PermReq {
  event_id: string;
  case_id: string;
  action: string;
  reason: string;
}

export interface RunResult {
  passed: number;
  failed: number;
  total: number;
}

/**
 * 把执行控制台的 SSE 逻辑封装成 hook,供用例表「原地执行」使用。
 * statuses: 按 case_id 索引的实时状态 + 步骤流。
 */
export function useSuiteRun(suiteId: string | undefined) {
  const [statuses, setStatuses] = useState<Record<string, CaseRunState>>({});
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [permission, setPermission] = useState<PermReq | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const stop = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const start = useCallback(
    async (caseIds: string[]) => {
      if (!suiteId) return;
      stop();
      // 预置所有用例为 pending
      const seed: Record<string, CaseRunState> = {};
      for (const cid of caseIds)
        seed[cid] = { status: "pending", steps: [], phases: [] };
      setStatuses(seed);
      setRunning(true);
      setDone(false);
      setResult(null);
      setError(null);

      try {
        const { run_id } = await apiPost<{ run_id: string }>(
          `/suites/${suiteId}/run`,
        );
        setRunId(run_id);

        const es = new EventSource(
          sseUrl(`/suites/${suiteId}/stream?run_id=${run_id}`),
        );
        esRef.current = es;

        const upd = (caseId: string, fn: (c: CaseRunState) => CaseRunState) =>
          setStatuses((prev) => ({
            ...prev,
            [caseId]: fn(
              prev[caseId] ?? { status: "pending", steps: [], phases: [] },
            ),
          }));

        es.addEventListener("case_start", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          upd(d.case_id as string, (c) => ({
            ...c,
            status: "running",
            steps: [],
            phases: [],
          }));
        });

        es.addEventListener("phase", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          upd(d.case_id as string, (c) => ({
            ...c,
            phases: [
              ...c.phases.filter((p) => p.phase !== d.phase),
              { phase: d.phase as string, label: d.label as string },
            ],
          }));
        });

        es.addEventListener("step_change", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          upd(d.case_id as string, (c) => ({
            ...c,
            steps: [
              ...c.steps.filter((s) => s.index !== d.step_index),
              {
                index: d.step_index as number,
                status: d.status as string,
                description: d.description as string,
              },
            ],
          }));
        });

        es.addEventListener("step_done", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          upd(d.case_id as string, (c) => ({
            ...c,
            steps: c.steps.map((s) =>
              s.index === d.step_index ? { ...s, status: "done" } : s,
            ),
          }));
        });

        es.addEventListener("case_result", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          upd(d.case_id as string, (c) => ({
            ...c,
            status: d.verdict === "PASS" ? "passed" : "failed",
          }));
        });

        es.addEventListener("permission", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (d) setPermission(d as unknown as PermReq);
        });

        es.addEventListener("suite_done", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (d)
            setResult({
              passed: d.passed as number,
              failed: d.failed as number,
              total: d.total as number,
            });
          setDone(true);
          setRunning(false);
          es.close();
        });

        es.addEventListener("error", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (d?.message) {
            setError(d.message as string);
            setRunning(false);
          }
          // 否则 EventSource 会自动重连,不处理
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setRunning(false);
      }
    },
    [suiteId, stop],
  );

  return {
    statuses,
    running,
    done,
    result,
    permission,
    error,
    runId,
    start,
    stop,
    clearPermission: () => setPermission(null),
  };
}
