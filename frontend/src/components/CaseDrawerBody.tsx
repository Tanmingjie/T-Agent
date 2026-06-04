import { useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";
import {
  CheckCircle,
  XCircle,
  Loader2,
  MinusCircle,
  Clock,
  Wrench,
  ImageOff,
  ListChecks,
  FileText,
} from "lucide-react";
import type { CaseRunState, CaseRunStatus } from "../hooks/useSuiteRun";

interface CaseInfo {
  id: string;
  name: string;
  steps: string[];
  preconditions: string[];
  expected: string[];
}

interface AssertionResult {
  type: string;
  target: string;
  expected?: string | null;
  status: string; // pass | fail | skipped
  actual?: string | null;
  reason?: string | null;
}

interface StepDetail {
  step_no: number;
  model_output: {
    reasoning: string;
    intent?: string;
    tool_name: string;
    tool_input: Record<string, unknown>;
  };
  action_result: {
    tool_result: string;
    url: string;
    screenshot: string | null;
    duration_ms: number;
  };
}

interface SpecAssertion {
  type: string;
  target: string;
  expected?: string | null;
}

interface SpecStep {
  action: string;
  target: string;
  data?: string | null;
  expect?: SpecAssertion[];
}

interface TestSpec {
  case_id: string;
  name: string;
  base_url: string;
  given?: SpecStep[];
  steps?: SpecStep[];
  assertions?: SpecAssertion[];
}

interface CaseResult {
  passed: boolean;
  final_result: string;
  token_usage: number;
  heal_count: number;
  case_assertions: AssertionResult[];
  history: StepDetail[];
  spec?: TestSpec | null;
}

interface CodeResp {
  files: Record<string, string>;
}

const NOISE = ["browser_snapshot", "mark_step_done"];

function prettyTool(tool: string, input: Record<string, unknown>): string {
  const s = (k: string) => (input[k] != null ? String(input[k]) : "");
  switch (tool) {
    case "browser_navigate":
      return `导航到 ${s("url")}`;
    case "browser_click":
      return `点击 ${s("element") || s("target")}`;
    case "browser_type":
      return `在 ${s("element") || s("target")} 输入 “${s("text")}”`;
    case "browser_evaluate":
      return `求值 ${s("function") || s("selector")}`.trim();
    default:
      return tool;
  }
}

function prettyLive(desc: string): string {
  const m = desc.match(/^(\w+)\((.*)\)$/s);
  if (!m) return desc;
  const [, tool, args] = m;
  const get = (k: string) => {
    const mm = args.match(new RegExp(`${k}=([^,)]+)`));
    return mm ? mm[1].trim() : "";
  };
  const input = {
    url: get("url"),
    element: get("element"),
    target: get("target"),
    text: get("text"),
    function: get("function"),
    selector: get("selector"),
  };
  return prettyTool(tool, input);
}

const pad3 = (n: number) => String(n).padStart(3, "0");

const STATUS_PILL: Record<CaseRunStatus, { label: string; icon: React.ReactNode; cls: string }> = {
  pending: { label: "未执行", icon: <Clock size={15} />, cls: "text-gray-400" },
  running: {
    label: "执行中",
    icon: <Loader2 size={15} className="animate-spin" />,
    cls: "text-brand-600",
  },
  passed: { label: "通过", icon: <CheckCircle size={15} />, cls: "text-brand-700" },
  failed: { label: "失败", icon: <XCircle size={15} />, cls: "text-red-600" },
  healing: { label: "自愈中", icon: <Wrench size={15} />, cls: "text-amber-600" },
};

function AssertIcon({ status }: { status: string }) {
  if (status === "pass") return <CheckCircle size={15} className="text-brand-600" />;
  if (status === "fail") return <XCircle size={15} className="text-red-600" />;
  return <MinusCircle size={15} className="text-gray-300" />;
}

/** 截图,加载失败时回退占位。 */
function Shot({ src, alt }: { src: string; alt: string }) {
  const [err, setErr] = useState(false);
  if (err)
    return (
      <div className="flex flex-col items-center justify-center py-16 text-gray-300">
        <ImageOff size={32} className="mb-2" />
        <span className="text-sm">无截图</span>
      </div>
    );
  return (
    <img
      src={src}
      alt={alt}
      onError={() => setErr(true)}
      className="w-full rounded-lg border border-gray-200"
    />
  );
}

const ACTION_LABEL: Record<string, string> = {
  navigate: "打开",
  fill: "输入",
  type: "输入",
  click: "点击",
  select: "选择",
  hover: "悬停",
  wait: "等待",
};

function specLine(s: SpecStep): string {
  const verb = ACTION_LABEL[s.action] ?? s.action;
  const data = s.data ? ` “${s.data}”` : "";
  return `${verb} ${s.target}${data}`.trim();
}

/** 列表区块:标题 + 条目,空则不渲染。供右栏「用例信息」用。 */
function ListBlock({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className="text-[11px] font-medium uppercase tracking-wider text-gray-400 mb-1.5">
        {title}
      </h4>
      <ul className="space-y-1">
        {items.map((t, i) => (
          <li key={i} className="text-sm text-gray-600 leading-snug">
            • {t}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 右栏「用例信息」视图:预置条件 + 预期结果 + 完整 TestSpec(翻译产物)。 */
function InfoView({
  caseInfo,
  spec,
}: {
  caseInfo: CaseInfo;
  spec?: TestSpec | null;
}) {
  const given = spec?.given ?? [];
  const steps = spec?.steps ?? [];
  // 断言聚合:用例级 + 各步 expect(与后端 collect_assertions 一致;LLM 常把断言
  // 放进 step.expect 而非用例级,只渲染用例级会显示为空)。按语义键去重。
  const seen = new Set<string>();
  const assertions = [
    ...(spec?.assertions ?? []),
    ...[...given, ...steps].flatMap((s) => s.expect ?? []),
  ].filter((a) => {
    const k = `${a.type}|${a.target}|${a.expected ?? ""}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <ListBlock title="预置条件" items={caseInfo.preconditions} />
      <ListBlock title="预期结果" items={caseInfo.expected} />

      <section className="border-t border-gray-200 pt-5">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-surface-900 mb-1">
          <FileText size={15} className="text-brand-600" />
          执行规格 (TestSpec)
        </h3>
        <p className="text-xs text-gray-400 mb-4">
          AI 把用例翻译成的结构化执行规格,断言在此一次性结构化。可据此核对翻译是否准确。
        </p>
        {!spec ? (
          <p className="text-sm text-gray-400">本次执行无规格记录(历史数据或执行前)。</p>
        ) : (
          <div className="space-y-4">
            <ListBlock title="前置 (given)" items={given.map(specLine)} />
            <ListBlock title="步骤 (steps)" items={steps.map(specLine)} />
            <ListBlock
              title="断言 (assertions)"
              items={assertions.map(
                (a) =>
                  `[${a.type}] ${a.target}${
                    a.expected != null && a.expected !== "" ? ` == ${a.expected}` : ""
                  }`,
              )}
            />
            {given.length === 0 && steps.length === 0 && assertions.length === 0 && (
              <p className="text-sm text-gray-400">规格为空</p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

type Selection = { kind: "info" } | { kind: "result" } | { kind: "step"; no: number };

interface DisplayStep {
  no: number; // 用于截图 URL (history 用 step_no);live/spec 用序号
  label: string;
  hasShot: boolean;
  state: "done" | "running" | "spec";
  reasoning?: string;
  toolResult?: string;
  url?: string;
}

export default function CaseDrawerBody({
  suiteId,
  runId,
  caseInfo,
  status,
  liveState,
}: {
  suiteId: string;
  runId: string | null;
  caseInfo: CaseInfo;
  status: CaseRunStatus;
  liveState?: CaseRunState;
}) {
  const [result, setResult] = useState<CaseResult | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sel, setSel] = useState<Selection>({ kind: "info" });
  const [rightTab, setRightTab] = useState<"preview" | "code">("preview");

  useEffect(() => {
    setResult(null);
    setCode(null);
    setSel({ kind: "info" });
    setRightTab("preview");
    if (!runId) return;
    setLoading(true);
    Promise.all([
      apiGet<CaseResult>(`/suites/${suiteId}/runs/${runId}/cases/${caseInfo.id}/result`).catch(
        () => null,
      ),
      apiGet<CodeResp>(`/suites/${suiteId}/runs/${runId}/cases/${caseInfo.id}/code`).catch(
        () => null,
      ),
    ])
      .then(([r, c]) => {
        if (r) {
          setResult(r);
          setSel({ kind: "result" }); // 已执行完成,默认定位测试结果
        }
        if (c) setCode(Object.values(c.files).join("\n\n") || null);
      })
      .finally(() => setLoading(false));
  }, [suiteId, runId, caseInfo.id]);

  // 统一步骤列表:history(有截图) > live(实时) > spec(规格)
  const steps: DisplayStep[] = useMemo(() => {
    if (result?.history?.length) {
      return result.history
        .filter((s) => !NOISE.includes(s.model_output.tool_name))
        .map((s) => ({
          no: s.step_no,
          label:
            s.model_output.intent ||
            prettyTool(s.model_output.tool_name, s.model_output.tool_input),
          hasShot: !!s.action_result.screenshot,
          state: "done" as const,
          reasoning: s.model_output.reasoning,
          toolResult: s.action_result.tool_result,
          url: s.action_result.url,
        }));
    }
    if (liveState?.steps?.length) {
      return [...liveState.steps]
        .filter((s) => !NOISE.some((n) => s.description.includes(n)))
        .sort((a, b) => a.index - b.index)
        .map((s) => ({
          no: s.index + 1,
          label: prettyLive(s.description),
          hasShot: false,
          state: s.status === "done" ? ("done" as const) : ("running" as const),
        }));
    }
    return caseInfo.steps.map((s, i) => ({
      no: i + 1,
      label: s,
      hasShot: false,
      state: "spec" as const,
    }));
  }, [result, liveState, caseInfo.steps]);

  const finalShotNo = useMemo(() => {
    const withShot = steps.filter((s) => s.hasShot);
    return withShot.length ? withShot[withShot.length - 1].no : null;
  }, [steps]);

  const pill = STATUS_PILL[status] ?? STATUS_PILL.pending;
  const shotUrl = (no: number) =>
    `/api/screenshots/${runId}/${caseInfo.id}/step_${pad3(no)}.png`;
  const selStep = sel.kind === "step" ? steps.find((s) => s.no === sel.no) : undefined;

  return (
    <div className="flex flex-col h-full">
      {/* Big header */}
      <div className="px-6 py-4 border-b border-gray-200 shrink-0">
        <h2 className="text-lg font-semibold text-surface-900">{caseInfo.name}</h2>
        <div className={`mt-1 inline-flex items-center gap-1.5 text-sm ${pill.cls}`}>
          {pill.icon}
          {pill.label}
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* ── Left pane ── */}
        <aside className="w-80 shrink-0 border-r border-gray-200 overflow-auto p-4 space-y-5">
          {/* 用例信息(预置/预期/TestSpec)→ 右栏宽栏展示 */}
          <button
            onClick={() => setSel({ kind: "info" })}
            className={`w-full text-left rounded-lg border p-3 transition-colors ${
              sel.kind === "info"
                ? "border-brand-300 bg-brand-50/60"
                : "border-gray-200 hover:bg-gray-50"
            }`}
          >
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-brand-600 shrink-0" />
              <span className="text-sm font-medium text-surface-900">用例信息</span>
            </div>
            <p className="mt-1 text-xs text-gray-500 line-clamp-2">
              预置条件 · 预期结果 · 执行规格 (TestSpec)
            </p>
          </button>

          {/* Test result card */}
          {result && (
            <button
              onClick={() => setSel({ kind: "result" })}
              className={`w-full text-left rounded-lg border p-3 transition-colors ${
                sel.kind === "result"
                  ? result.passed
                    ? "border-brand-300 bg-brand-50/60"
                    : "border-red-300 bg-red-50/60"
                  : "border-gray-200 hover:bg-gray-50"
              }`}
            >
              <div className="flex items-center gap-2">
                {result.passed ? (
                  <CheckCircle size={16} className="text-brand-600 shrink-0" />
                ) : (
                  <XCircle size={16} className="text-red-600 shrink-0" />
                )}
                <span className="text-sm font-medium text-surface-900">测试结果</span>
              </div>
              <p className="mt-1 text-xs text-gray-500 line-clamp-2">
                {result.passed ? "测试通过，无断言失败。" : "测试失败，查看断言详情。"}
              </p>
            </button>
          )}

          {/* Steps */}
          <section>
            <h4 className="text-[11px] font-medium uppercase tracking-wider text-gray-400 mb-2 flex items-center gap-1.5">
              <ListChecks size={13} /> 步骤
            </h4>
            <div className="space-y-1.5">
              {steps.map((s) => {
                const active = sel.kind === "step" && sel.no === s.no;
                return (
                  <button
                    key={`${s.no}-${s.label}`}
                    onClick={() => setSel({ kind: "step", no: s.no })}
                    className={`w-full text-left rounded-lg border p-2.5 flex items-start gap-2 cursor-pointer transition-colors ${
                      active
                        ? "border-brand-300 bg-brand-50/60"
                        : "border-gray-200 hover:bg-gray-50"
                    }`}
                  >
                    <span className="mt-0.5 shrink-0">
                      {s.state === "running" ? (
                        <Loader2 size={14} className="text-gray-400 animate-spin" />
                      ) : s.state === "done" ? (
                        <CheckCircle size={14} className="text-brand-600" />
                      ) : (
                        <span className="w-4 h-4 rounded bg-gray-100 text-gray-500 text-[10px] flex items-center justify-center">
                          {s.no}
                        </span>
                      )}
                    </span>
                    <span className="text-sm text-gray-700 leading-snug">{s.label}</span>
                  </button>
                );
              })}
              {steps.length === 0 && (
                <p className="text-sm text-gray-400">暂无步骤</p>
              )}
            </div>
          </section>
        </aside>

        {/* ── Right pane ── */}
        <section className="flex-1 overflow-auto bg-gray-50/40">
          {loading ? (
            <div className="p-6 text-sm text-gray-400">加载中…</div>
          ) : sel.kind === "info" ? (
            <InfoView caseInfo={caseInfo} spec={result?.spec} />
          ) : sel.kind === "step" && selStep ? (
            /* Step view: screenshot + detail */
            <div className="p-6 space-y-4">
              <h3 className="text-sm font-medium text-surface-900">{selStep.label}</h3>
              {runId ? (
                <div className="max-w-xl">
                  <Shot src={shotUrl(selStep.no)} alt={selStep.label} />
                </div>
              ) : (
                <p className="text-sm text-gray-400">该步骤无截图（执行后生成）</p>
              )}
              {selStep.url && (
                <p className="text-xs text-gray-400 break-all">URL: {selStep.url}</p>
              )}
              {selStep.toolResult && (
                <div>
                  <h4 className="text-[11px] font-medium uppercase tracking-wider text-gray-400 mb-1">
                    执行结果
                  </h4>
                  <pre className="text-xs bg-white border border-gray-200 rounded-md p-3 whitespace-pre-wrap text-gray-600 max-h-48 overflow-auto">
                    {selStep.toolResult}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            /* Result view: Preview/Code tabs + assertions */
            <div className="flex flex-col min-h-full">
              {/* Right tabs */}
              <div className="px-6 border-b border-gray-200 bg-white flex gap-4 shrink-0">
                {(["preview", "code"] as const).map((t) => (
                  <button
                    key={t}
                    onClick={() => setRightTab(t)}
                    className={`py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
                      rightTab === t
                        ? "border-brand-600 text-surface-900"
                        : "border-transparent text-gray-500 hover:text-surface-900"
                    }`}
                  >
                    {t === "preview" ? "Preview" : "代码"}
                  </button>
                ))}
              </div>

              <div className="p-6 space-y-5">
                {rightTab === "preview" ? (
                  runId && finalShotNo != null ? (
                    <div className="max-w-xl">
                      <Shot src={shotUrl(finalShotNo)} alt="最终态截图" />
                    </div>
                  ) : (
                    <p className="text-sm text-gray-400">
                      {runId ? "无最终态截图。" : "该用例尚无执行记录。"}
                    </p>
                  )
                ) : code ? (
                  <pre className="text-xs bg-surface-900 text-gray-100 p-4 rounded-lg overflow-auto leading-relaxed">
                    <code>{code}</code>
                  </pre>
                ) : (
                  <p className="text-sm text-gray-400">
                    {runId ? "暂无生成代码（执行通过后生成）。" : "执行后可查看生成代码。"}
                  </p>
                )}

                {/* Assertions + healing (TestSprite: 在 preview 下方) */}
                {result && (
                  <section className="border-t border-gray-200 pt-4">
                    <h4 className="text-[11px] font-medium uppercase tracking-wider text-gray-400 mb-2">
                      断言结果
                    </h4>
                    {result.case_assertions.length === 0 ? (
                      <p className="text-sm text-gray-400">无断言记录</p>
                    ) : (
                      <ul className="space-y-1.5">
                        {result.case_assertions.map((a, i) => (
                          <li key={i} className="flex items-start gap-2 text-sm">
                            <span className="mt-0.5 shrink-0">
                              <AssertIcon status={a.status} />
                            </span>
                            <span className="text-gray-700">
                              <span className="text-gray-400">[{a.type}]</span> {a.target}
                              {a.expected != null && a.expected !== "" && (
                                <span className="text-gray-400"> == {a.expected}</span>
                              )}
                              {a.status === "fail" && (a.actual || a.reason) && (
                                <span className="block text-xs text-red-600 mt-0.5">
                                  实际: {a.actual ?? "—"}
                                  {a.reason ? ` · ${a.reason}` : ""}
                                </span>
                              )}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="mt-3 text-xs text-gray-400">
                      Token {result.token_usage} · 自愈 {result.heal_count} 次
                    </p>
                  </section>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
