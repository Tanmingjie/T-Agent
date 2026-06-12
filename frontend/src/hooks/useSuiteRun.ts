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
  screenshot?: string | null; // 该步真实截图文件名(None=无图,如快照/失败步)
  prompt?: string | null; // 本轮发给 LLM 的请求(供执行中「查看 prompt」)
  reasoning?: string; // 该步「思考过程」(后端权威 reasoning,缺则流式 thinkStream 兜底)
  toolResult?: string; // 工具观察文本(过程时间线展示)
  url?: string; // 该步执行后页面 URL
  healCount?: number; // 该步操作侧自愈次数
}

export interface PhaseStatus {
  phase: string;
  label: string;
}

export interface CaseRunState {
  status: CaseRunStatus;
  steps: StepStatus[];
  phases: PhaseStatus[]; // 生命周期阶段流(翻译/执行/断言/代码),最后一个为当前进行中
  spec?: unknown; // 翻译阶段完成后实时推送的 TestSpec(执行中也能看执行规格)
}

// 流式文本(spec 翻译增量 / 当前步思考增量):**高频**逐 token 更新。**不进 `statuses`
// React state**——否则每个 delta 都会重渲染整个 SuiteCasesPage(用例表 + 抽屉)造成卡顿。
// 改放外部 store,只有订阅的流式叶子节点(useSyncExternalStore)随之重渲染。
export interface StreamText {
  spec: string;
  think: string;
}
const EMPTY_STREAM: StreamText = { spec: "", think: "" };

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

  // 外部 store:流式文本(per-case),与高频重渲染隔离。
  const streamRef = useRef<Record<string, StreamText>>({});
  const streamListeners = useRef<Map<string, Set<() => void>>>(new Map());

  // 供组件订阅(稳定引用)。getStream 返回的对象在两次通知间保持稳定引用。
  const subscribeStream = useCallback(
    (cid: string, cb: () => void) => {
      let set = streamListeners.current.get(cid);
      if (!set) {
        set = new Set();
        streamListeners.current.set(cid, set);
      }
      set.add(cb);
      return () => set!.delete(cb);
    },
    [],
  );
  const getStream = useCallback(
    (cid: string): StreamText => streamRef.current[cid] ?? EMPTY_STREAM,
    [],
  );

  const stop = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
  }, []);

  const start = useCallback(
    // caseId 给定时只跑该单条用例(抽屉「执行」按钮),否则跑 caseIds 代表的整套件
    async (caseIds: string[], caseId?: string) => {
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
        const runPath = caseId
          ? `/suites/${suiteId}/run?case_id=${encodeURIComponent(caseId)}`
          : `/suites/${suiteId}/run`;
        const { run_id } = await apiPost<{ run_id: string }>(runPath);
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

        // 流式文本 store 的就地 mutator(只改 ref + 通知订阅者,不碰 React state)。
        const notifyStream = (cid: string) =>
          streamListeners.current.get(cid)?.forEach((l) => l());
        const pushStream = (cid: string, key: keyof StreamText, delta: string) => {
          const cur = streamRef.current[cid] ?? EMPTY_STREAM;
          streamRef.current[cid] = { ...cur, [key]: cur[key] + delta };
          notifyStream(cid);
        };
        const resetStream = (cid: string, key?: keyof StreamText) => {
          const cur = streamRef.current[cid] ?? EMPTY_STREAM;
          streamRef.current[cid] = key
            ? { ...cur, [key]: "" }
            : { ...EMPTY_STREAM };
          notifyStream(cid);
        };

        es.addEventListener("case_start", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          resetStream(d.case_id as string); // 清流式文本(外部 store)
          upd(d.case_id as string, (c) => ({
            ...c,
            status: "running",
            steps: [],
            phases: [],
          }));
        });

        // 高频 delta → 只进外部 store + 通知订阅者,不触发 setStatuses(消整页重渲染)
        es.addEventListener("spec_delta", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          pushStream(d.case_id as string, "spec", (d.delta as string) ?? "");
        });

        es.addEventListener("think_delta", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          pushStream(d.case_id as string, "think", (d.delta as string) ?? "");
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

        es.addEventListener("spec_ready", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          upd(d.case_id as string, (c) => ({ ...c, spec: d.spec }));
        });

        es.addEventListener("step_change", (e) => {
          const d = safeParse((e as MessageEvent).data);
          if (!d) return;
          const cid = d.case_id as string;
          // 本步落定 → 把累积的思考流**定格**到该步(retain,可回看),再清空给下一步
          const accumThink = (streamRef.current[cid] ?? EMPTY_STREAM).think;
          resetStream(cid, "think");
          upd(cid, (c) => {
            const existing = c.steps.find((s) => s.index === d.step_index);
            return {
              ...c,
              steps: [
                ...c.steps.filter((s) => s.index !== d.step_index),
                {
                  index: d.step_index as number,
                  status: d.status as string,
                  description: d.description as string,
                  screenshot: (d.screenshot as string | null) ?? null,
                  prompt: (d.prompt as string | null) ?? null,
                  // 本步思考:优先后端权威 reasoning,缺则用本次累积的思考流兜底
                  reasoning:
                    (d.reasoning as string) || accumThink || existing?.reasoning || "",
                  toolResult: (d.tool_result as string) ?? undefined,
                  url: (d.url as string) ?? undefined,
                  healCount: (d.heal_count as number) ?? 0,
                },
              ],
            };
          });
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
    subscribeStream,
    getStream,
    clearPermission: () => setPermission(null),
  };
}
