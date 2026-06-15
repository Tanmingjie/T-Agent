import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { apiGet, apiPut } from "../api/client";
import {
  CheckCircle,
  XCircle,
  Loader2,
  MinusCircle,
  Clock,
  Wrench,
  ImageOff,
  FileText,
  Copy,
  Check,
  Play,
  AlertTriangle,
  ChevronDown,
} from "lucide-react";
import type { CaseRunState, CaseRunStatus } from "../hooks/useSuiteRun";

interface PreconditionItem {
  text: string;
  type: string; // state_hook | action_step | ambiguous | ignore
  hook_ref?: string | null;
  confidence?: number;
  confirmed_by_user?: boolean;
}

interface CaseInfo {
  id: string;
  name: string;
  steps: string[];
  preconditions: string[];
  expected: string[];
  precondition_items?: PreconditionItem[];
}

interface AssertionResult {
  type: string;
  target: string;
  expected?: string | null;
  status: string; // pass | fail | skipped
  actual?: string | null;
  reason?: string | null;
  ai_judged?: boolean; // 由 llm_judge 兜底判定(低置信)→ 与结构化绿区分,使 false green 可见
  healed?: boolean; // 经自愈重定位后才通过 → 与结构化绿区分(自愈绿)
  heal_note?: string | null; // 自愈摘要(重定位到哪个 target / 策略)
  step_no?: number; // 步骤级断言:属于第几步(在该步子页面即时验证)
  phase?: string; // step=步骤级即时验证 / final=终态用例级
}

interface StepDetail {
  step_no: number;
  model_output: {
    reasoning: string;
    intent?: string;
    prompt?: string;
    tool_name: string;
    tool_input: Record<string, unknown>;
  };
  action_result: {
    tool_result: string;
    url: string;
    screenshot: string | null;
    heal_attempts?: unknown[];
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
  expect_text?: string;
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

interface CaseMetrics {
  tokens?: Record<string, number>;
  execution?: {
    stop_reason?: string;
    iterations?: number;
    max_steps?: number;
    idle_nudges?: number;
    complete?: boolean;
    done_steps?: number;
    total_steps?: number;
    action_steps?: number;
  };
  healing?: { action?: number; assertion?: number };
  assertions?: { pass?: number; fail?: number; skipped?: number; ai_judged?: number; total?: number };
}

interface CaseResult {
  passed: boolean;
  final_result: string;
  token_usage: number;
  heal_count: number;
  case_assertions: AssertionResult[];
  history: StepDetail[];
  spec?: TestSpec | null;
  metrics?: CaseMetrics | null;
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

const STATUS_PILL: Record<
  CaseRunStatus,
  { label: string; icon: React.ReactNode; cls: string }
> = {
  pending: { label: "未执行", icon: <Clock size={15} />, cls: "text-gray-400" },
  running: {
    label: "执行中",
    icon: <Loader2 size={15} className="animate-spin" />,
    cls: "text-blue-600",
  },
  passed: {
    label: "通过",
    icon: <CheckCircle size={15} />,
    cls: "text-brand-700",
  },
  failed: { label: "失败", icon: <XCircle size={15} />, cls: "text-red-600" },
  healing: {
    label: "自愈中",
    icon: <Wrench size={15} />,
    cls: "text-amber-600",
  },
};

function AssertIcon({ status }: { status: string }) {
  if (status === "pass")
    return <CheckCircle size={15} className="text-brand-600" />;
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
  const expect = s.expect_text ? ` → 预期:${s.expect_text}` : "";
  return `${verb} ${s.target}${data}${expect}`.trim();
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

const PRECOND_TYPE_META: Record<
  string,
  { label: string; cls: string }
> = {
  state_hook: { label: "状态声明 → Hook", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  action_step: { label: "操作步骤 → Given", cls: "bg-blue-50 text-blue-700 border-blue-200" },
  ambiguous: { label: "模糊 · 待确认", cls: "bg-amber-50 text-amber-700 border-amber-300" },
  ignore: { label: "忽略", cls: "bg-gray-100 text-gray-500 border-gray-200" },
};

/** 预置条件三分类:展示分类结果,模糊项标黄,用户可选 Hook/Given/忽略(规格 §3.2)。 */
function PreconditionBlock({
  suiteId,
  caseId,
  items,
}: {
  suiteId: string;
  caseId: string;
  items: PreconditionItem[];
}) {
  const [rows, setRows] = useState<PreconditionItem[]>(items);
  const [saving, setSaving] = useState<number | null>(null);
  useEffect(() => setRows(items), [items]);

  const setType = useCallback(
    async (index: number, type: string) => {
      setSaving(index);
      const hook_ref =
        type === "state_hook" ? rows[index].hook_ref ?? "LoginHook" : null;
      try {
        await apiPut(
          `/suites/${suiteId}/cases/${caseId}/precondition-item`,
          { index, type, hook_ref },
        );
        setRows((prev) =>
          prev.map((r, i) =>
            i === index ? { ...r, type, hook_ref, confirmed_by_user: true } : r,
          ),
        );
      } finally {
        setSaving(null);
      }
    },
    [suiteId, caseId, rows],
  );

  return (
    <section>
      <h3 className="text-sm font-semibold text-surface-900 mb-1">预置条件分类</h3>
      <p className="text-xs text-gray-400 mb-3">
        AI 三分类:状态声明→Hook / 操作步骤→Given / 模糊项标黄待你确认。可随时改,确认后下次跳过重判。
      </p>
      <ul className="space-y-2">
        {rows.map((it, i) => {
          const meta = PRECOND_TYPE_META[it.type] ?? PRECOND_TYPE_META.ambiguous;
          const pending = it.type === "ambiguous" && !it.confirmed_by_user;
          return (
            <li
              key={i}
              className={`rounded-md border px-3 py-2 ${
                pending ? "border-amber-300 bg-amber-50/60" : "border-gray-200 bg-white"
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <span className="text-sm text-surface-900">{it.text}</span>
                <span
                  className={`shrink-0 rounded border px-1.5 py-0.5 text-[11px] ${meta.cls}`}
                >
                  {meta.label}
                  {it.confirmed_by_user ? " ✓" : ""}
                </span>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <select
                  className="rounded border border-gray-300 bg-white px-2 py-1 text-xs text-surface-800"
                  value={["state_hook", "action_step", "ignore"].includes(it.type) ? it.type : ""}
                  disabled={saving === i}
                  onChange={(e) => e.target.value && setType(i, e.target.value)}
                >
                  <option value="" disabled>
                    选择处理方式…
                  </option>
                  <option value="state_hook">状态声明 → Hook</option>
                  <option value="action_step">操作步骤 → Given</option>
                  <option value="ignore">忽略</option>
                </select>
                {it.type === "state_hook" && it.hook_ref && (
                  <span className="text-xs text-gray-500">Hook: {it.hook_ref}</span>
                )}
                {saving === i && <Loader2 size={13} className="animate-spin text-gray-400" />}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 右栏「用例信息」视图:预置条件 + 预期结果 + 完整 TestSpec(翻译产物)。 */
function InfoView({
  suiteId,
  caseInfo,
  spec,
}: {
  suiteId: string;
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
      {caseInfo.precondition_items && caseInfo.precondition_items.length > 0 ? (
        <PreconditionBlock
          suiteId={suiteId}
          caseId={caseInfo.id}
          items={caseInfo.precondition_items}
        />
      ) : (
        <ListBlock title="预置条件" items={caseInfo.preconditions} />
      )}
      <ListBlock title="测试步骤" items={caseInfo.steps} />
      <ListBlock title="预期结果" items={caseInfo.expected} />

      <section className="border-t border-gray-200 pt-5">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-surface-900 mb-1">
          <FileText size={15} className="text-brand-600" />
          执行规格 (TestSpec)
        </h3>
        <p className="text-xs text-gray-400 mb-4">
          AI
          把用例翻译成的结构化执行规格,断言在此一次性结构化。可据此核对翻译是否准确。
        </p>
        {!spec ? (
          <p className="text-sm text-gray-400">
            本次执行无规格记录(历史数据或执行前)。
          </p>
        ) : (
          <div className="space-y-4">
            <ListBlock title="前置 (given)" items={given.map(specLine)} />
            <ListBlock title="步骤 (steps)" items={steps.map(specLine)} />
            <ListBlock
              title="断言 (assertions)"
              items={assertions.map(
                (a) =>
                  `[${a.type}] ${a.target}${
                    a.expected != null && a.expected !== ""
                      ? ` == ${a.expected}`
                      : ""
                  }`,
              )}
            />
            {given.length === 0 &&
              steps.length === 0 &&
              assertions.length === 0 && (
                <p className="text-sm text-gray-400">规格为空</p>
              )}
          </div>
        )}
      </section>
    </div>
  );
}

/** 浅色代码块:行号 + 限高滚动 + 复制,主题与界面统一(参考 TestSprite)。 */
function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const lines = code.replace(/\n$/, "").split("\n");
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用时静默 */
    }
  }
  return (
    <div className="max-w-3xl rounded-lg border border-gray-200 bg-white overflow-hidden">
      {/* 工具条 */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-100 bg-gray-50/60">
        <span className="text-[11px] font-medium text-gray-400">生成代码</span>
        <button
          onClick={copy}
          className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-surface-900 transition-colors"
        >
          {copied ? (
            <>
              <Check size={13} className="text-brand-600" /> 已复制
            </>
          ) : (
            <>
              <Copy size={13} /> 复制
            </>
          )}
        </button>
      </div>
      <div className="overflow-auto max-h-[28rem]">
        <pre className="text-xs leading-relaxed font-mono">
          <code className="block">
            {lines.map((ln, i) => (
              <div key={i} className="flex hover:bg-gray-50">
                <span className="sticky left-0 w-10 shrink-0 select-none bg-white text-right pr-3 text-gray-300 border-r border-gray-100">
                  {i + 1}
                </span>
                <span className="pl-3 pr-4 text-gray-700 whitespace-pre">
                  {ln || " "}
                </span>
              </div>
            ))}
          </code>
        </pre>
      </div>
    </div>
  );
}

// 流式文本订阅:从外部 store(useSuiteRun)按 caseId+key 订阅单条流式文本。**只有该叶子
// 节点**随 token 重渲染,不带动 TimelineView/抽屉/整页 → 根治流式期间的整页卡顿。
type StreamApi = {
  subscribeStream: (caseId: string, cb: () => void) => () => void;
  getStream: (caseId: string) => { spec: string; think: string };
};

function useStream(
  api: StreamApi | undefined,
  caseId: string,
  key: "spec" | "think",
): string {
  const subscribe = useCallback(
    (cb: () => void) => (api ? api.subscribeStream(caseId, cb) : () => {}),
    [api, caseId],
  );
  const snapshot = useCallback(
    () => (api ? api.getStream(caseId)[key] : ""),
    [api, caseId, key],
  );
  return useSyncExternalStore(subscribe, snapshot);
}

// 流式文本只看尾部:超长(>8000 字)只渲染尾部窗口,界住单帧排版成本为常数——流式期间
// 用户只关注正在流出的尾巴,完整内容另有结构化展示(spec→用例信息;思考→落定后定格)。
const STREAM_TAIL = 8000;

// 流式 pre:文本增长时自动滚到底(让用户看到正在流出的尾部,而非固定高度里的顶部旧文),
// 并对超长流做尾部窗口截断。auto-scroll 的 scrollHeight 读取已被 rAF 合到 ≤60fps。
function StreamPre({ text, className }: { text: string; className: string }) {
  const ref = useRef<HTMLPreElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text]);
  const shown = text.length > STREAM_TAIL ? text.slice(-STREAM_TAIL) : text;
  return (
    <pre ref={ref} className={className}>
      {shown}
      <BlinkCursor />
    </pre>
  );
}

// 翻译阶段流式 spec(叶子,自订阅):active 且有流时显示流式 pre,否则回退提示。
function SpecStream({
  api,
  caseId,
  active,
  hasSpec,
}: {
  api?: StreamApi;
  caseId: string;
  active: boolean;
  hasSpec: boolean;
}) {
  const spec = useStream(api, caseId, "spec");
  if (active && spec)
    return (
      <StreamPre
        text={spec}
        className="ml-6 mt-1 max-h-56 overflow-auto rounded-md bg-gray-50 border border-gray-200 p-3 text-xs leading-relaxed text-gray-700 whitespace-pre-wrap break-words"
      />
    );
  return (
    <p className="ml-6 mt-1 text-xs text-gray-400">
      {hasSpec
        ? "已生成执行规格（详见左侧「用例信息」）。"
        : "等待翻译…"}
    </p>
  );
}

// 执行期「思考中」当前步流式(叶子,自订阅)。
function ThinkStream({ api, caseId }: { api?: StreamApi; caseId: string }) {
  const think = useStream(api, caseId, "think");
  return think ? (
    <StreamPre
      text={think}
      className="rounded bg-gray-50 border border-gray-200 p-2 text-xs leading-relaxed text-gray-700 whitespace-pre-wrap break-words max-h-56 overflow-auto"
    />
  ) : (
    <p className="text-xs text-gray-400">决策下一步…</p>
  );
}

// 单个时间线步骤(React.memo:仅当自身 props 变化才重渲染)。已落定步显示其定格 reasoning;
// 实时思考流由独立的「思考中」叶子节点(ThinkStream)承载,不进此处 → 步骤行不随 token 重渲染。
const TimelineStep = memo(function TimelineStep({
  step,
  open,
  promptShown,
  onToggle,
  onTogglePrompt,
  shotUrl,
}: {
  step: DisplayStep;
  open: boolean;
  promptShown: boolean;
  onToggle: (no: number) => void;
  onTogglePrompt: (no: number) => void;
  shotUrl: (no: number) => string;
}) {
  const running = step.state === "running";
  const thinking = step.reasoning;
  return (
    <li className="relative ml-5">
      <span className="absolute -left-[1.42rem] top-1.5">
        {running ? (
          <Loader2 size={14} className="text-blue-600 animate-spin" />
        ) : (
          <CheckCircle size={14} className="text-brand-600" />
        )}
      </span>
      <button
        onClick={() => onToggle(step.no)}
        className="w-full text-left flex items-center gap-2"
      >
        <span className="text-sm text-surface-900 font-medium">{step.label}</span>
        {!!step.healCount && step.healCount > 0 && (
          <span className="inline-flex items-center gap-0.5 text-[10px] text-blue-700 bg-blue-50 border border-blue-200 rounded px-1">
            <Wrench size={10} /> 自愈{step.healCount}
          </span>
        )}
        <ChevronDown
          size={14}
          className={`ml-auto text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="mt-1.5 space-y-2">
          {thinking && (
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 mb-1">
                思考
              </p>
              <pre className="rounded bg-gray-50 border border-gray-200 p-2 text-xs leading-relaxed text-gray-700 whitespace-pre-wrap break-words max-h-56 overflow-auto">
                {thinking}
                {running && <BlinkCursor />}
              </pre>
            </div>
          )}
          {step.url && (
            <p className="text-[11px] text-gray-400 break-all">URL: {step.url}</p>
          )}
          {step.hasShot && (
            <div className="max-w-sm">
              <Shot src={shotUrl(step.no)} alt={step.label} />
            </div>
          )}
          {step.prompt && (
            <div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => onTogglePrompt(step.no)}
                  className="text-[11px] font-medium text-brand-700 hover:text-brand-800"
                >
                  {promptShown ? "收起 prompt" : "查看 prompt"}
                </button>
                {promptShown && (
                  <button
                    onClick={() => navigator.clipboard?.writeText(step.prompt ?? "")}
                    className="text-[11px] text-gray-400 hover:text-gray-600"
                  >
                    复制
                  </button>
                )}
              </div>
              {promptShown && (
                <pre className="mt-1 text-[11px] bg-surface-900 text-gray-100 border border-gray-800 rounded-md p-3 whitespace-pre-wrap max-h-80 overflow-auto leading-relaxed">
                  {step.prompt}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
},
areTimelineStepEqual);

// 自定义比较:step_change 时 steps 数组整体重建,每个 DisplayStep 都是**新对象**(默认
// 身份比较会让所有步骤重渲染)。改按字段浅比 → 只有内容真变的那一步(及新步)重渲染,
// 其余已落定步原地不动。onToggle/onTogglePrompt/shotUrl 均为稳定引用,逐一比对即可。
function areTimelineStepEqual(
  a: {
    step: DisplayStep;
    open: boolean;
    promptShown: boolean;
    onToggle: (no: number) => void;
    onTogglePrompt: (no: number) => void;
    shotUrl: (no: number) => string;
  },
  b: typeof a,
): boolean {
  if (
    a.open !== b.open ||
    a.promptShown !== b.promptShown ||
    a.onToggle !== b.onToggle ||
    a.onTogglePrompt !== b.onTogglePrompt ||
    a.shotUrl !== b.shotUrl
  )
    return false;
  const x = a.step;
  const y = b.step;
  return (
    x.no === y.no &&
    x.label === y.label &&
    x.state === y.state &&
    x.reasoning === y.reasoning &&
    x.url === y.url &&
    x.hasShot === y.hasShot &&
    x.healCount === y.healCount &&
    x.prompt === y.prompt
  );
}

// 过程时间线:把整条用例的执行**全过程**收在一处,执行中流式、执行后回溯。
// 顺序:翻译规格 → 逐步(思考/工具/自愈/截图)→ 结构化断言 → 最终结果。
function TimelineView({
  steps,
  liveState,
  result,
  isRunning,
  shotUrl,
  code,
  streamApi,
  caseId,
}: {
  steps: DisplayStep[];
  liveState?: CaseRunState;
  result: CaseResult | null;
  isRunning: boolean;
  shotUrl: (no: number) => string;
  code: string | null;
  streamApi?: StreamApi;
  caseId: string;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [promptOpen, setPromptOpen] = useState<Set<number>>(new Set());
  const [showCode, setShowCode] = useState(false);
  const togglePrompt = useCallback(
    (no: number) =>
      setPromptOpen((p) => {
        const n = new Set(p);
        n.has(no) ? n.delete(no) : n.add(no);
        return n;
      }),
    [],
  );
  const toggle = useCallback(
    (no: number) =>
      setExpanded((p) => {
        const n = new Set(p);
        n.has(no) ? n.delete(no) : n.add(no);
        return n;
      }),
    [],
  );
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const phase = liveState?.phases?.[liveState.phases.length - 1]?.phase;

  // 只展示真实执行步骤(done/running),不展示翻译前的占位步骤(spec)——翻译后步骤会变,
  // 初始不该先摆一份会被替换的「测试步骤」。
  const realSteps = useMemo(() => steps.filter((s) => s.state !== "spec"), [steps]);
  const executing = isRunning && phase === "executing";

  // 执行中自动滚到底:**仅在新步骤落定时**触发(不随思考流逐 token 触发,否则
  // smooth 滚动风暴会让界面卡顿、点击无响应)。
  useEffect(() => {
    // 立即滚(非 smooth):smooth 动画在快速连续落步时会排队、加重卡顿
    if (isRunning) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [realSteps.length, isRunning]);

  const incomplete = /执行未完成|停因=max_steps/.test(result?.final_result ?? "");

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      {/* ── 1. 翻译规格 ── */}
      <section>
        <TimelineHeader
          label="翻译用例为执行规格 (TestSpec)"
          done={!!result?.spec || !!liveState?.spec || realSteps.length > 0}
          // 开跑时(尚无 phase 事件)翻译就是当前活动阶段;收到 executing 后才算翻译完
          active={isRunning && (!phase || phase === "spec")}
        />
        <SpecStream
          api={streamApi}
          caseId={caseId}
          active={isRunning && phase === "spec"}
          hasSpec={!!result?.spec || !!liveState?.spec}
        />
      </section>

      {/* ── 2. 执行过程(逐步) ── */}
      <section>
        <TimelineHeader
          label="驱动浏览器逐步执行"
          done={!!result}
          // 仅在确实进入「执行」阶段才转圈(开跑时 phase 未到 executing,不该先显执行中)
          active={isRunning && phase === "executing"}
        />
        <ol className="ml-1 mt-2 border-l border-gray-200 space-y-3">
          {realSteps.length === 0 && !executing && (
            <li className="ml-5 text-xs text-gray-400">
              {isRunning ? "等待执行…" : "尚无步骤"}
            </li>
          )}
          {realSteps.map((s) => (
            <TimelineStep
              key={`${s.no}-${s.label}`}
              step={s}
              open={expanded.has(s.no)}
              promptShown={promptOpen.has(s.no)}
              onToggle={toggle}
              onTogglePrompt={togglePrompt}
              shotUrl={shotUrl}
            />
          ))}
          {/* 进行中:当前正在决策的下一步,逐 token 流式(独立订阅,不重渲染上方步骤);
              step_change 落定即并入上方步骤 */}
          {executing && (
            <li className="relative ml-5">
              <span className="absolute -left-[1.42rem] top-1.5">
                <Loader2 size={14} className="text-blue-600 animate-spin" />
              </span>
              <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 mb-1">
                思考中
              </p>
              <ThinkStream api={streamApi} caseId={caseId} />
            </li>
          )}
        </ol>
      </section>

      {/* ── 3. 结构化断言(执行完成后) ── */}
      {result && (
        <section>
          <TimelineHeader label="结构化断言裁决" done active={false} />
          {incomplete && (
            <div className="ml-6 mt-1 mb-2 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>
                执行未完成、步骤没走完，用例已直接判 <strong>FAIL</strong>；下面断言在半路
                页面上跑，仅供参考、不作裁决依据。
              </span>
            </div>
          )}
          <ul className="ml-6 mt-1 space-y-1.5">
            {result.case_assertions.length === 0 && (
              <li className="text-sm text-gray-400">无断言记录</li>
            )}
            {result.case_assertions.map((a, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                <span className="mt-0.5 shrink-0">
                  <AssertIcon status={a.status} />
                </span>
                <span className="text-gray-700">
                  {a.phase === "step" && a.step_no != null && (
                    <span
                      className="mr-1.5 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-surface-100 text-surface-600 border border-surface-200 align-middle"
                      title="步骤级断言:在该步所处子页面即时验证"
                    >
                      步骤{a.step_no}
                    </span>
                  )}
                  <span className="text-gray-400">[{a.type}]</span> {a.target}
                  {a.expected != null && a.expected !== "" && (
                    <span className="text-gray-400"> == {a.expected}</span>
                  )}
                  {a.ai_judged && (
                    <span className="ml-1.5 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-amber-50 text-amber-700 border border-amber-200 align-middle">
                      AI判定·低置信
                    </span>
                  )}
                  {a.healed && (
                    <span
                      className="ml-1.5 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium bg-blue-50 text-blue-700 border border-blue-200 align-middle"
                      title={a.heal_note ? `经自愈重定位后通过:${a.heal_note}` : "经自愈重定位后通过"}
                    >
                      已自愈
                    </span>
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
        </section>
      )}

      {/* ── 4. 最终结果 ── */}
      {result && (
        <section>
          <TimelineHeader
            label="最终结果"
            done
            active={false}
            icon={
              result.passed ? (
                <CheckCircle size={15} className="text-brand-600" />
              ) : (
                <XCircle size={15} className="text-red-600" />
              )
            }
          />
          <div className="ml-6 mt-1 space-y-2">
            <p className={`text-sm font-medium ${result.passed ? "text-brand-700" : "text-red-600"}`}>
              {result.passed ? "测试通过" : "测试失败"}
            </p>
            <p className="text-xs text-gray-400">
              Token {result.token_usage} · 自愈 {result.heal_count} 次
              {(() => {
                const m = result.final_result?.match(/停因=([^)]+)/);
                return m ? ` · 停因 ${m[1]}` : "";
              })()}
            </p>
            {result.metrics && <MetricsPanel m={result.metrics} />}
            {code && (
              <div>
                <button
                  onClick={() => setShowCode((v) => !v)}
                  className="text-xs font-medium text-brand-700 hover:text-brand-800"
                >
                  {showCode ? "收起生成代码" : "查看生成代码"}
                </button>
                {showCode && (
                  <div className="mt-1.5">
                    <CodeBlock code={code} />
                  </div>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      <div ref={bottomRef} />
    </div>
  );
}

// 分阶段成本/质量指标(#6):token 分阶段、执行健康度、断言裁决分布(含 AI 兜底占比)。
const PHASE_LABEL: Record<string, string> = {
  spec: "翻译",
  executing: "执行",
  asserting: "断言",
  codegen: "代码",
  scanning: "扫描",
};

function MetricsPanel({ m }: { m: CaseMetrics }) {
  const tokens = m.tokens ?? {};
  const ex = m.execution ?? {};
  const a = m.assertions ?? {};
  const heal = m.healing ?? {};
  const phaseTokens = Object.entries(tokens).filter(([k, v]) => k !== "total" && v > 0);
  return (
    <details className="text-xs text-gray-500">
      <summary className="cursor-pointer select-none text-brand-700 hover:text-brand-800">
        执行指标
      </summary>
      <div className="mt-1.5 space-y-1.5 rounded border border-gray-100 bg-gray-50 p-2">
        {phaseTokens.length > 0 && (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            <span className="text-gray-400">Token:</span>
            {phaseTokens.map(([k, v]) => (
              <span key={k}>
                {PHASE_LABEL[k] ?? k} <b className="text-gray-700">{v}</b>
              </span>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
          <span className="text-gray-400">执行:</span>
          <span>
            轮数 <b className="text-gray-700">{ex.iterations ?? "-"}</b>/{ex.max_steps ?? "-"}
          </span>
          <span>
            步骤 <b className="text-gray-700">{ex.done_steps ?? "-"}</b>/{ex.total_steps ?? "-"}
          </span>
          {(ex.idle_nudges ?? 0) > 0 && (
            <span className="text-amber-600">哑火续推 {ex.idle_nudges}</span>
          )}
          {ex.complete === false && <span className="text-red-600">执行未完成</span>}
          {ex.stop_reason && <span>停因 {ex.stop_reason}</span>}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
          <span className="text-gray-400">断言:</span>
          <span className="text-brand-700">通过 {a.pass ?? 0}</span>
          {(a.fail ?? 0) > 0 && <span className="text-red-600">失败 {a.fail}</span>}
          {(a.skipped ?? 0) > 0 && <span>跳过 {a.skipped}</span>}
          {(a.ai_judged ?? 0) > 0 && (
            <span className="text-amber-600" title="由 llm_judge 兜底判定(低置信,false-green 风险面)">
              AI 兜底 {a.ai_judged}
            </span>
          )}
          {((heal.action ?? 0) > 0 || (heal.assertion ?? 0) > 0) && (
            <span className="text-gray-400">
              自愈 操作 {heal.action ?? 0}/断言 {heal.assertion ?? 0}
            </span>
          )}
        </div>
      </div>
    </details>
  );
}

function TimelineHeader({
  label,
  done,
  active,
  icon,
}: {
  label: string;
  done: boolean;
  active: boolean;
  icon?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-2">
      {icon ??
        (active ? (
          <Loader2 size={15} className="text-blue-600 animate-spin" />
        ) : done ? (
          <CheckCircle size={15} className="text-brand-600" />
        ) : (
          <span className="w-[15px] h-[15px] rounded-full border-2 border-gray-300 inline-block" />
        ))}
      <h4 className={`text-sm font-semibold ${active ? "text-blue-700" : "text-surface-900"}`}>
        {label}
      </h4>
    </div>
  );
}

function BlinkCursor() {
  return (
    <span className="inline-block w-1.5 h-3.5 bg-blue-500 animate-pulse align-middle ml-0.5" />
  );
}

type Selection = { kind: "info" } | { kind: "result" };

interface DisplayStep {
  no: number; // 用于截图 URL (history 用 step_no);live/spec 用序号
  label: string;
  hasShot: boolean;
  state: "done" | "running" | "spec";
  reasoning?: string;
  toolResult?: string;
  url?: string;
  prompt?: string;
  healCount?: number;
}

export default function CaseDrawerBody({
  suiteId,
  runId,
  caseInfo,
  status,
  liveState,
  onRun,
  runDisabled,
  subscribeStream,
  getStream,
}: {
  suiteId: string;
  runId: string | null;
  caseInfo: CaseInfo;
  status: CaseRunStatus;
  liveState?: CaseRunState;
  onRun?: (caseId: string) => void;
  runDisabled?: boolean;
  subscribeStream?: StreamApi["subscribeStream"];
  getStream?: StreamApi["getStream"];
}) {
  // 流式订阅 api(稳定引用):仅当 hook 暴露的订阅函数变化才重建。
  const streamApi = useMemo<StreamApi | undefined>(
    () =>
      subscribeStream && getStream
        ? { subscribeStream, getStream }
        : undefined,
    [subscribeStream, getStream],
  );
  const [result, setResult] = useState<CaseResult | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection>({ kind: "info" });

  const isRunning = status === "running" || status === "healing";
  // 进行中的结果请求:重新加载时先 abort 上一次,避免 /result+/code 在 HTTP/1.1
  // 连接池上堆积 pending(SSE 长连接已占 1 个槽,反复点开会很快耗尽 6 连接上限)。
  const reqRef = useRef<AbortController | null>(null);

  const loadResult = useCallback(
    (autoSelect: boolean) => {
      if (!runId) return;
      reqRef.current?.abort(); // 取消上一次未完成的结果/代码请求
      const ac = new AbortController();
      reqRef.current = ac;
      Promise.all([
        apiGet<CaseResult>(
          `/suites/${suiteId}/runs/${runId}/cases/${caseInfo.id}/result`,
          ac.signal,
        ).catch(() => null),
        apiGet<CodeResp>(
          `/suites/${suiteId}/runs/${runId}/cases/${caseInfo.id}/code`,
          ac.signal,
        ).catch(() => null),
      ])
        .then(([r, c]) => {
          if (ac.signal.aborted) return; // 已被取代,丢弃结果
          if (r) {
            setResult(r);
            if (autoSelect) setSel({ kind: "result" }); // 已执行完成,默认定位测试结果
          }
          if (c) setCode(Object.values(c.files).join("\n\n") || null);
        });
    },
    [suiteId, runId, caseInfo.id],
  );

  // 打开抽屉 / 切换 run:复位并拉一次结果。执行中**不请求** /result、/code——
  // 记录要等用例跑完才落库,执行中请求必然 404(无效请求);改走 running 视图,
  // 待 status→passed/failed 时由下方 effect 拉取。
  useEffect(() => {
    setResult(null);
    setCode(null);
    setSel(isRunning ? { kind: "result" } : { kind: "info" });
    if (!isRunning) loadResult(true);
    return () => reqRef.current?.abort(); // 关抽屉/切换时取消在途请求
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suiteId, runId, caseInfo.id]);

  // 用例在本次会话内跑完(running→passed/failed):重新拉结果,免得抽屉停在"执行中"。
  // 只在**状态真正变化**时响应:跳过初次挂载,否则会与上面的挂载 effect 重复请求一次。
  const prevStatus = useRef<CaseRunStatus | null>(null);
  useEffect(() => {
    const prev = prevStatus.current;
    prevStatus.current = status;
    if (prev === null) return; // 初次挂载由上面的 effect 处理,这里不重复拉
    if (status === "passed" || status === "failed") loadResult(true);
    if (isRunning) setSel({ kind: "result" }); // 执行中默认停在结果栏(running 视图)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

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
          prompt: s.model_output.prompt,
          healCount: s.action_result.heal_attempts?.length ?? 0,
        }));
    }
    if (liveState?.steps?.length) {
      return [...liveState.steps]
        .filter((s) => !NOISE.some((n) => s.description.includes(n)))
        .sort((a, b) => a.index - b.index)
        .map((s) => ({
          // index 即后端全局 step_no(与截图文件名 step_NNN.png 一致),不能 +1
          no: s.index,
          label: prettyLive(s.description),
          // 用后端回传的真实截图字段判断有无图:失败/重试步、快照步并不落图,
          // 一律假设有图会去取不存在的 step_NNN.png 报 404 显示「无截图」
          hasShot: !!s.screenshot,
          state: s.status === "done" ? ("done" as const) : ("running" as const),
          reasoning: s.reasoning,
          toolResult: s.toolResult,
          url: s.url,
          prompt: s.prompt ?? undefined,
          healCount: s.healCount,
        }));
    }
    return caseInfo.steps.map((s, i) => ({
      no: i + 1,
      label: s,
      hasShot: false,
      state: "spec" as const,
    }));
    // 仅依赖 steps 数组(其引用在 think_delta 高频更新时保持稳定),不依赖整个 liveState
    // → 思考流逐 token 推进时不重算步骤列表,消除流式期间点击切换的卡顿。
  }, [result, liveState?.steps, caseInfo.steps]);

  const pill = STATUS_PILL[status] ?? STATUS_PILL.pending;
  // 稳定引用(供时间线步骤 memo);随 run/用例变化才重建
  const shotUrl = useCallback(
    (no: number) => `/api/screenshots/${runId}/${caseInfo.id}/step_${pad3(no)}.png`,
    [runId, caseInfo.id],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Big header */}
      <div className="px-6 py-4 border-b border-gray-200 shrink-0 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-surface-900">
            {caseInfo.name}
          </h2>
          <div
            className={`mt-1 inline-flex items-center gap-1.5 text-sm ${pill.cls}`}
          >
            {pill.icon}
            {pill.label}
          </div>
        </div>
        {onRun && (
          <button
            onClick={() => onRun(caseInfo.id)}
            disabled={runDisabled || isRunning}
            className="shrink-0 inline-flex items-center gap-1.5 px-3.5 py-2 rounded-md text-sm font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isRunning ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <Play size={15} />
            )}
            {isRunning ? "执行中" : "执行"}
          </button>
        )}
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
              <span className="text-sm font-medium text-surface-900">
                用例信息
              </span>
            </div>
            <p className="mt-1 text-xs text-gray-500 line-clamp-2">
              预置条件 · 预期结果 · 执行规格 (TestSpec)
            </p>
          </button>

          {/* Test result card(执行中显示转圈占位,参考 TestSprite) */}
          {(result || isRunning) && (
            <button
              onClick={() => setSel({ kind: "result" })}
              className={`w-full text-left rounded-lg border p-3 transition-colors ${
                sel.kind === "result"
                  ? !result
                    ? "border-blue-300 bg-blue-50/60"
                    : result.passed
                      ? "border-brand-300 bg-brand-50/60"
                      : "border-red-300 bg-red-50/60"
                  : "border-gray-200 hover:bg-gray-50"
              }`}
            >
              <div className="flex items-center gap-2">
                {!result ? (
                  <Loader2
                    size={16}
                    className="text-blue-600 shrink-0 animate-spin"
                  />
                ) : result.passed ? (
                  <CheckCircle size={16} className="text-brand-600 shrink-0" />
                ) : (
                  <XCircle size={16} className="text-red-600 shrink-0" />
                )}
                <span className="text-sm font-medium text-surface-900">
                  测试结果
                </span>
              </div>
              <p className="mt-1 text-xs text-gray-500 line-clamp-2">
                {!result
                  ? "执行中…"
                  : result.passed
                    ? "测试通过，无断言失败。"
                    : "测试失败，查看断言详情。"}
              </p>
            </button>
          )}

        </aside>

        {/* ── Right pane ── */}
        {/* 不再用 loading 全屏遮罩:InfoView 由 caseInfo 同步可得,结果到达再切到时间线,
            避免抽屉滑入(200ms)期间「InfoView→加载中→时间线」的多次整面切换造成卡顿。 */}
        <section className="flex-1 overflow-auto bg-canvas">
          {sel.kind === "info" ? (
            // 执行中结果未落库,用实时推送的 spec_ready 作回退,翻译后即可看执行规格
            <InfoView
              suiteId={suiteId}
              caseInfo={caseInfo}
              spec={result?.spec ?? (liveState?.spec as TestSpec | undefined)}
            />
          ) : (
            /* 过程时间线:执行中流式、执行后回溯,全过程一处可见 */
            <TimelineView
              steps={steps}
              liveState={liveState}
              result={result}
              isRunning={isRunning}
              shotUrl={shotUrl}
              code={code}
              streamApi={streamApi}
              caseId={caseInfo.id}
            />
          )}
        </section>
      </div>
    </div>
  );
}
