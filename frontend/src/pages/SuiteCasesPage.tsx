import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, apiPost } from "../api/client";
import { useSuiteRunCtx } from "../components/SuiteLayout";
import {
  Upload,
  Play,
  Search,
  FileSpreadsheet,
  Loader2,
  CheckCircle,
  XCircle,
  Clock,
  Square,
  Sparkles,
  Check,
} from "lucide-react";
import Drawer from "../components/Drawer";
import CaseDrawerBody from "../components/CaseDrawerBody";
import PermissionDialog from "../components/PermissionDialog";
import SpecReviewDialog, {
  EditableTestSpec,
} from "../components/SpecReviewDialog";
import { CaseRunStatus } from "../hooks/useSuiteRun";

interface Case {
  id: string;
  name: string;
  steps: string[];
  preconditions: string[];
  expected: string[];
}

interface RunLite {
  id: string;
  started_at: number;
}

/** 去掉用例 id 的套件前缀(后端为避免跨套件同号冲突加的 `{suiteId}--` 前缀),还原 Excel 编号展示。 */
function caseNo(id: string): string {
  const i = id.indexOf("--");
  return i >= 0 ? id.slice(i + 2) : id;
}

interface RunOverview {
  cases: { case_id: string; passed: boolean }[];
}

interface SuiteResp {
  name: string;
  base_url: string;
  project_id?: string;
  cases: Case[];
  runs: RunLite[];
}

interface ProjectSkill {
  name: string;
  description: string;
}

const STATUS_META: Record<
  CaseRunStatus,
  { label: string; icon: React.ReactNode; cls: string }
> = {
  pending: { label: "未执行", icon: <Clock size={14} />, cls: "text-gray-400" },
  running: {
    label: "执行中",
    icon: <Loader2 size={14} className="animate-spin" />,
    cls: "text-blue-600",
  },
  passed: { label: "通过", icon: <CheckCircle size={14} />, cls: "text-brand-700" },
  failed: { label: "失败", icon: <XCircle size={14} />, cls: "text-red-600" },
};

function StatusCell({ status }: { status: CaseRunStatus }) {
  const m = STATUS_META[status] ?? STATUS_META.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm ${m.cls}`}>
      {m.icon}
      {m.label}
    </span>
  );
}

// 用例行(memo):执行期 step_change 更新 statuses 会重渲染本页,但 `c`(用例对象)与
// onSelect 引用稳定、status 是基元 → 仅**状态真变的那一行**重渲染,其余行原地不动。
const CaseRow = memo(function CaseRow({
  c,
  status,
  onSelect,
  checked,
  selectionDisabled,
  onToggle,
}: {
  c: Case;
  status: CaseRunStatus;
  onSelect: (c: Case) => void;
  checked: boolean;
  selectionDisabled: boolean;
  onToggle: (caseId: string) => void;
}) {
  return (
    <tr
      onClick={() => onSelect(c)}
      className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70 cursor-pointer transition-colors"
    >
      <td className="pl-5 pr-1 py-3.5 w-12">
        <input
          type="checkbox"
          checked={checked}
          disabled={selectionDisabled}
          aria-label={`选择用例 ${c.name}`}
          onClick={(event) => event.stopPropagation()}
          onChange={() => onToggle(c.id)}
          className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500 disabled:opacity-50"
        />
      </td>
      <td className="px-5 py-3.5">
        <StatusCell status={status} />
      </td>
      <td className="px-5 py-3.5 font-medium text-surface-900">{c.name}</td>
      <td className="px-5 py-3.5 font-mono text-xs text-gray-500">
        {caseNo(c.id)}
      </td>
      <td className="px-5 py-3.5 text-gray-500">{c.steps.length}</td>
    </tr>
  );
});

export default function SuiteCasesPage() {
  const { id } = useParams<{ id: string }>();
  const [suite, setSuite] = useState<SuiteResp | null>(null);
  const [uploading, setUploading] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Case | null>(null);
  const [selectedCaseIds, setSelectedCaseIds] = useState<Set<string>>(() => new Set());
  // 最近一次历史 run 的逐用例裁决(caseId → passed),供未实时执行时回填状态列
  const [pastStatus, setPastStatus] = useState<Record<string, CaseRunStatus>>({});
  // 项目 skill + 执行前勾选(强制加载,一次性随本次 run)
  const [skills, setSkills] = useState<ProjectSkill[]>([]);
  const [forceSkills, setForceSkills] = useState<string[]>([]);
  // 执行确认弹框:点「执行」后弹出,选 skill 再确认开始。记录本次要跑的目标
  // caseIds=null=全量；非空列表=单条或部分执行。runModal=null=未打开。
  type RunTarget = {
    caseIds: string[] | null;
    source: "all" | "selected" | "single";
  };
  const [runModal, setRunModal] = useState<RunTarget | null>(null);
  const [manualSpecReview, setManualSpecReview] = useState(false);
  const [retranslateMemory, setRetranslateMemory] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [forceLowQuality, setForceLowQuality] = useState(false);
  const [qualityOverrideReason, setQualityOverrideReason] = useState("");
  const previewAbortRef = useRef<AbortController | null>(null);
  const [reviewTarget, setReviewTarget] = useState<RunTarget | null>(null);
  const [reviewSpecs, setReviewSpecs] = useState<EditableTestSpec[] | null>(null);

  useEffect(() => {
    setForceLowQuality(false);
    setQualityOverrideReason("");
  }, [runModal?.source, runModal?.caseIds?.join(","), forceSkills.join(",")]);

  // 执行状态来自布局层的 RunProvider(切 tab 不丢失;高频更新只重渲染本页消费者,
  // 不带动侧栏/面包屑)。不再由本页持有 SSE。
  const run = useSuiteRunCtx();

  async function load() {
    setSuite(await apiGet(`/suites/${id}`));
  }
  useEffect(() => {
    load();
  }, [id]);

  // 项目 skill 清单(供执行前勾选强制加载)。无项目/无 skill → 不显示选择入口。
  useEffect(() => {
    const pid = suite?.project_id;
    if (!pid) {
      setSkills([]);
      return;
    }
    apiGet<ProjectSkill[]>(`/projects/${pid}/skills`)
      .then((sk) => setSkills(sk))
      .catch(() => setSkills([]));
  }, [suite?.project_id]);

  // 拉最近一次 run 的逐用例结果,使列表状态与抽屉(同一 run)保持一致
  useEffect(() => {
    const runs = (suite?.runs ?? []).slice().sort((a, b) => b.started_at - a.started_at);
    const latest = runs[0]?.id;
    if (!latest) {
      setPastStatus({});
      return;
    }
    apiGet<RunOverview>(`/suites/${id}/runs/${latest}`)
      .then((ov) => {
        const m: Record<string, CaseRunStatus> = {};
        for (const c of ov.cases) m[c.case_id] = c.passed ? "passed" : "failed";
        setPastStatus(m);
      })
      .catch(() => setPastStatus({}));
  }, [id, suite?.runs]);
  // SSE 生命周期由 SuiteLayout 持有;本页切换不再 stop(否则切 tab 会断流)。

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      await apiPost(`/suites/${id}/upload`, fd);
      await load();
    } catch (err) {
      alert("上传失败: " + (err instanceof Error ? err.message : String(err)));
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  const cases = suite?.cases ?? [];
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return cases;
    return cases.filter(
      (c) => c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q),
    );
  }, [cases, query]);
  const filteredIds = useMemo(() => filtered.map((c) => c.id), [filtered]);
  const allFilteredSelected =
    filteredIds.length > 0 && filteredIds.every((caseId) => selectedCaseIds.has(caseId));
  const someFilteredSelected = filteredIds.some((caseId) => selectedCaseIds.has(caseId));

  useEffect(() => {
    const available = new Set(cases.map((c) => c.id));
    setSelectedCaseIds((previous) => {
      const next = new Set([...previous].filter((caseId) => available.has(caseId)));
      return next.size === previous.size ? previous : next;
    });
  }, [cases]);

  function statusOf(caseId: string): CaseRunStatus {
    // 新 Run 开始后只展示本轮状态,避免部分执行时混入上一次 Run 的裁决。
    if (run.running || run.done) {
      return run.statuses[caseId]?.status ?? "pending";
    }
    return pastStatus[caseId] ?? "pending";
  }

  // 稳定引用,供 memo 化的 CaseRow 比对(否则每次渲染新闭包会让所有行重渲染)。
  const onSelect = useCallback((c: Case) => setSelected(c), []);

  // 进度统计
  const tracked = Object.values(run.statuses);
  const completed = tracked.filter(
    (c) => c.status === "passed" || c.status === "failed",
  ).length;
  const activeCount = tracked.filter((c) => c.status === "running").length;
  const showProgress = run.running || run.done;
  const runTotal = run.result?.total || run.totalCases || tracked.length;

  // 点「执行」:先弹框确认 Midscene 执行 + 可选 skill,再跑整套件。
  function startRun() {
    const caseIds = cases.filter((c) => selectedCaseIds.has(c.id)).map((c) => c.id);
    setRunModal(
      caseIds.length > 0
        ? { caseIds, source: "selected" }
        : { caseIds: null, source: "all" },
    );
  }

  // 单用例执行(抽屉右上角「执行」按钮):先弹框确认 Midscene 执行 + 可选 skill。
  function runOne(caseId: string) {
    setRunModal({ caseIds: [caseId], source: "single" });
  }

  const toggleCaseSelection = useCallback((caseId: string) => {
    setSelectedCaseIds((previous) => {
      const next = new Set(previous);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });
  }, []);

  function toggleFilteredSelection() {
    if (run.running || filteredIds.length === 0) return;
    setSelectedCaseIds((previous) => {
      const next = new Set(previous);
      if (allFilteredSelected) filteredIds.forEach((caseId) => next.delete(caseId));
      else filteredIds.forEach((caseId) => next.add(caseId));
      return next;
    });
  }

  // 弹框「开始执行」确认:按选中的目标 + 勾选的 skill 触发。
  async function confirmRun() {
    const target = runModal;
    if (!target) return;
    const retranslateCaseIds = retranslateMemory
      ? target.caseIds ?? cases.map((c) => c.id)
      : [];
    if (forceLowQuality && !qualityOverrideReason.trim()) {
      alert("强制执行低分用例必须填写原因。");
      return;
    }
    if (manualSpecReview) {
      const controller = new AbortController();
      previewAbortRef.current = controller;
      setPreviewing(true);
      try {
        const result = await apiPost<{ specs: EditableTestSpec[] }>(
          `/suites/${id}/spec-preview`,
          { case_ids: target.caseIds, skill_names: forceSkills },
          null,
          controller.signal,
        );
        setReviewTarget(target);
        setReviewSpecs(result.specs);
        setRunModal(null);
      } catch (error) {
        if (!controller.signal.aborted) {
          alert("翻译预览失败: " + (error instanceof Error ? error.message : String(error)));
        }
      } finally {
        if (previewAbortRef.current === controller) {
          previewAbortRef.current = null;
          setPreviewing(false);
        }
      }
      return;
    }
    setRunModal(null);
    executeTarget(target, undefined, retranslateCaseIds);
  }

  function executeTarget(
    target: RunTarget,
    approvedSpecs?: EditableTestSpec[],
    retranslateCaseIds?: string[],
  ) {
    const approved = Object.fromEntries(
      (approvedSpecs ?? []).map((spec) => [spec.case_id, spec]),
    );
    const firstCaseId = target.caseIds?.[0] ?? cases[0]?.id;
    const firstCase = cases.find((c) => c.id === firstCaseId);
    if (firstCase) setSelected(firstCase);
    run.start({
      caseIds: target.caseIds,
      allCaseIds: cases.map((c) => c.id),
      skillNames: forceSkills,
      approvedSpecs: approved,
      retranslateCaseIds: retranslateCaseIds ?? [],
      qualityGateEnabled: true,
      forceLowQualityCases: forceLowQuality,
      qualityOverrideReason,
    });
  }

  // 抽屉数据源:本次会话刚跑的 run 优先,否则取套件最近一次 run
  const selRun = selected ? run.statuses[selected.id] : undefined;
  const latestPastRun =
    (suite?.runs ?? []).slice().sort((a, b) => b.started_at - a.started_at)[0]
      ?.id ?? null;
  const effectiveRunId = run.runId ?? latestPastRun;

  return (
    <div>
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-surface-900">用例</h1>
          <p className="text-sm text-gray-500 mt-1">
            {suite?.base_url || "未设置 Base URL"}
          </p>
        </div>
        <div className="flex gap-2">
          <label
            className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-md text-sm font-medium border transition-colors cursor-pointer ${
              uploading
                ? "border-gray-200 text-gray-400 cursor-not-allowed"
                : "border-gray-300 text-gray-700 hover:bg-gray-50"
            }`}
          >
            {uploading ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Upload size={16} />
            )}
            {uploading ? "解析中…" : "上传 Excel"}
            <input
              type="file"
              accept=".xlsx,.xls"
              className="hidden"
              onChange={handleUpload}
              disabled={uploading}
            />
          </label>
          <button
            onClick={startRun}
            disabled={cases.length === 0 || run.running}
            className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-3.5 py-2 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {run.running ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Play size={16} />
            )}
            {run.running
              ? "执行中…"
              : selectedCaseIds.size > 0
                ? `执行所选 ${selectedCaseIds.size} 条`
                : "执行全部"}
          </button>
          {run.running && (
            <button
              onClick={run.requestStop}
              disabled={run.aborting}
              title="协作式停止:正在执行的那一步跑完即停"
              className="inline-flex items-center gap-1.5 border border-red-300 text-red-600 px-3.5 py-2 rounded-md text-sm font-medium hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Square size={15} />
              {run.aborting ? "停止中…" : "停止"}
            </button>
          )}
        </div>
      </div>

      {/* Progress bar (during/after run) */}
      {showProgress && (
        <div className="mb-5 p-4 bg-white border border-gray-200 rounded-lg">
          <div className="flex items-center justify-between mb-2 text-sm">
            <span className="text-surface-900 font-medium">
              {run.done
                ? "执行完成"
                : `${completed} / ${runTotal} 完成`}
              {!run.done && activeCount > 0 && (
                <span className="text-gray-500 font-normal">
                  {" "}
                  · {activeCount} 个执行中
                </span>
              )}
            </span>
            {run.result && (
              <span className="text-gray-500">
                <span className="text-brand-700">{run.result.passed} 通过</span>
                {run.result.failed > 0 && (
                  <span className="text-red-600"> · {run.result.failed} 失败</span>
                )}
              </span>
            )}
          </div>
          <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${
                run.done ? "bg-brand-600" : "bg-blue-500"
              }`}
              style={{
                width: `${runTotal ? (completed / runTotal) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      )}

      {run.error && (
        <div className="mb-5 p-3 bg-red-50 border border-red-200 rounded-md text-red-700 text-sm">
          {run.error}
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center gap-2 mb-3">
        <div className="relative">
          <Search
            size={15}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
          />
          <input
            className="w-64 border border-gray-300 rounded-md pl-9 pr-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
            placeholder="搜索用例…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <span className="text-xs text-gray-400 ml-auto">共 {cases.length} 条用例</span>
        {selectedCaseIds.size > 0 && (
          <>
            <span className="text-xs text-brand-700">已选择 {selectedCaseIds.size} 条</span>
            <button
              type="button"
              disabled={run.running}
              onClick={() => setSelectedCaseIds(new Set())}
              className="text-xs text-gray-500 hover:text-surface-900 disabled:opacity-50"
            >
              清空选择
            </button>
          </>
        )}
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500">
              <th className="pl-5 pr-1 py-3 font-medium w-12">
                <input
                  type="checkbox"
                  checked={allFilteredSelected}
                  disabled={run.running || filteredIds.length === 0}
                  aria-label="选择当前筛选结果"
                  ref={(element) => {
                    if (element) element.indeterminate = someFilteredSelected && !allFilteredSelected;
                  }}
                  onChange={toggleFilteredSelection}
                  className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500 disabled:opacity-50"
                />
              </th>
              <th className="px-5 py-3 font-medium w-28">状态</th>
              <th className="px-5 py-3 font-medium">名称</th>
              <th className="px-5 py-3 font-medium w-32">ID</th>
              <th className="px-5 py-3 font-medium w-20">步骤数</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-16 text-center">
                  <FileSpreadsheet
                    size={36}
                    className="mx-auto text-gray-300 mb-3"
                  />
                  <p className="text-gray-500 text-sm mb-1">
                    {query ? "没有匹配的用例" : "尚未上传用例"}
                  </p>
                  {!query && (
                    <p className="text-xs text-gray-400">
                      上传 Excel 文件以导入测试用例
                    </p>
                  )}
                </td>
              </tr>
            )}
            {filtered.map((c) => (
              <CaseRow
                key={c.id}
                c={c}
                status={statusOf(c.id)}
                onSelect={onSelect}
                checked={selectedCaseIds.has(c.id)}
                selectionDisabled={run.running}
                onToggle={toggleCaseSelection}
              />
            ))}
          </tbody>
        </table>
      </div>

      {/* Case drawer */}
      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        width="max-w-5xl"
        title={
          selected && (
            <p className="text-sm text-gray-500 truncate">{selected.name}</p>
          )
        }
      >
        {selected && (
            <CaseDrawerBody
              suiteId={id!}
              runId={effectiveRunId}
              caseInfo={selected}
              status={statusOf(selected.id)}
              liveState={selRun}
              qualityPending={
                run.running &&
                !!selRun &&
                !selRun.quality &&
                statusOf(selected.id) === "pending"
              }
              onRun={runOne}
              runDisabled={run.running}
              subscribeStream={run.subscribeStream}
            getStream={run.getStream}
          />
        )}
      </Drawer>

      {/* Permission dialog during run */}
      {run.permission && (
        <PermissionDialog
          eventId={run.permission.event_id}
          caseId={run.permission.case_id}
          action={run.permission.action}
          reason={run.permission.reason}
          suiteId={id!}
          onResolved={run.clearPermission}
        />
      )}

      {/* 执行确认弹框:选 skill 强制加载,再开始 */}
      {runModal !== null && (
        <div
          className="fixed inset-0 z-[60] bg-black/30 flex items-center justify-center p-4"
          onClick={() => {
            if (!previewing) setRunModal(null);
          }}
        >
          <div
            className="bg-white rounded-xl shadow-elevated w-full max-w-md max-h-[80vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 pt-5 pb-3 border-b border-gray-100">
              <h3 className="text-base font-semibold text-surface-900 flex items-center gap-2">
                <Sparkles size={16} className="text-brand-600" />
                选择执行方式
              </h3>
              <p className="text-xs text-gray-500 mt-1">
                {runModal.source === "single"
                  ? "只执行当前用例。"
                  : runModal.source === "selected"
                    ? `执行所选 ${runModal.caseIds?.length ?? 0} 条用例。`
                    : `执行全部 ${cases.length} 条用例。`}
                当前执行内核为 Midscene 视觉执行。
                {runModal.source === "selected" && " 如已配置登录准备用例，将自动先执行。"}
              </p>
            </div>
            <div className="flex-1 overflow-auto p-3 space-y-4">
              <div className="rounded-md border border-brand-100 bg-brand-50 px-3 py-2.5">
                <div className="text-sm font-medium text-surface-900">
                  Midscene 视觉执行
                </div>
                <div className="text-xs text-gray-500 mt-1 leading-5">
                  用阶段化 TestSpec 驱动浏览器,每阶段执行 aiAct 并用 aiAssert
                  验证预期。
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between gap-4 px-3 py-3 rounded-md border border-gray-200">
                  <div>
                    <div className="text-sm font-medium text-surface-900">人工确认执行规格</div>
                    <div className="text-xs text-gray-500 mt-1">
                      执行前审核并修改翻译生成的阶段步骤与预期。
                    </div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-label="人工确认执行规格"
                    aria-checked={manualSpecReview}
                    onClick={() => setManualSpecReview((value) => !value)}
                    className={`relative w-10 h-6 shrink-0 rounded-full transition-colors ${
                      manualSpecReview ? "bg-brand-600" : "bg-gray-300"
                    }`}
                  >
                    <span
                      className={`absolute left-0 top-1 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                        manualSpecReview ? "translate-x-5" : "translate-x-1"
                      }`}
                    />
                  </button>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between gap-4 px-3 py-3 rounded-md border border-gray-200">
                  <div>
                    <div className="text-sm font-medium text-surface-900">本次重新翻译</div>
                    <div className="text-xs text-gray-500 mt-1">
                      绕过已沉淀的成功执行规格和经验,重新生成 TestSpec。
                    </div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-label="本次重新翻译"
                    aria-checked={retranslateMemory}
                    onClick={() => setRetranslateMemory((value) => !value)}
                    className={`relative w-10 h-6 shrink-0 rounded-full transition-colors ${
                      retranslateMemory ? "bg-brand-600" : "bg-gray-300"
                    }`}
                  >
                    <span
                      className={`absolute left-0 top-1 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                        retranslateMemory ? "translate-x-5" : "translate-x-1"
                      }`}
                    />
                  </button>
                </div>
              </div>

              <div className="rounded-md border border-gray-200 px-3 py-3 space-y-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <div className="text-sm font-medium text-surface-900">
                      强制执行低分用例
                    </div>
                    <div className="text-xs text-gray-500 mt-1">
                      若已确认质量闸门阻断可忽略，可填写原因后绕过本次阻断。
                    </div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-label="强制执行低分用例"
                    aria-checked={forceLowQuality}
                    onClick={() => setForceLowQuality((value) => !value)}
                    className={`relative w-10 h-6 shrink-0 rounded-full transition-colors ${
                      forceLowQuality ? "bg-red-500" : "bg-gray-300"
                    }`}
                  >
                    <span
                      className={`absolute left-0 top-1 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${
                        forceLowQuality ? "translate-x-5" : "translate-x-1"
                      }`}
                    />
                  </button>
                </div>
                {forceLowQuality && (
                  <textarea
                    value={qualityOverrideReason}
                    onChange={(event) => setQualityOverrideReason(event.target.value)}
                    placeholder="填写强制执行原因，例如：试点调试 / 业务已确认可执行"
                    className="w-full min-h-16 rounded-md border border-red-200 px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-red-200"
                  />
                )}
              </div>

              <div>
                <div className="text-xs font-medium text-gray-500 px-1 mb-2">
                  强制加载 Skill
                </div>
                {skills.length === 0 ? (
                  <div className="px-3 py-4 rounded-md border border-dashed border-gray-200 text-xs text-gray-400">
                    当前项目暂无 Skill,将直接按执行内核运行。
                  </div>
                ) : (
                  <div className="space-y-1">
                    {skills.map((sk) => {
                      const on = forceSkills.includes(sk.name);
                      return (
                        <button
                          key={sk.name}
                          onClick={() =>
                            setForceSkills((prev) =>
                              on
                                ? prev.filter((n) => n !== sk.name)
                                : [...prev, sk.name],
                            )
                          }
                          className="w-full flex items-start gap-2.5 px-2.5 py-2 rounded-md hover:bg-gray-50 text-left"
                        >
                          <span
                            className={`mt-0.5 w-4 h-4 shrink-0 rounded border flex items-center justify-center ${
                              on
                                ? "bg-brand-600 border-brand-600 text-white"
                                : "border-gray-300"
                            }`}
                          >
                            {on && <Check size={12} />}
                          </span>
                          <span className="min-w-0">
                            <span className="block text-sm text-surface-900">
                              {sk.name}
                            </span>
                            {sk.description && (
                              <span className="block text-xs text-gray-400">
                                {sk.description}
                              </span>
                            )}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
            <div className="px-5 py-3 border-t border-gray-100 flex items-center justify-between">
              <span className="text-xs text-gray-400">
                Midscene · 已选 {forceSkills.length} 个 Skill
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    previewAbortRef.current?.abort();
                    setRunModal(null);
                  }}
                  className="px-3.5 py-2 rounded-md text-sm font-medium border border-gray-300 text-gray-700 hover:bg-gray-50 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={confirmRun}
                  disabled={previewing}
                  className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-3.5 py-2 rounded-md text-sm font-medium hover:bg-brand-700 transition-colors disabled:bg-brand-300"
                >
                  {previewing ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Play size={15} />
                  )}
                  {previewing
                    ? "正在翻译"
                    : manualSpecReview
                      ? "翻译并审核"
                      : "开始执行"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {reviewSpecs && reviewTarget && (
        <SpecReviewDialog
          specs={reviewSpecs}
          onCancel={() => {
            setReviewSpecs(null);
            setRunModal(reviewTarget);
          }}
          onConfirm={(specs) => {
            const target = reviewTarget;
            const retranslateCaseIds = retranslateMemory
              ? target.caseIds ?? cases.map((c) => c.id)
              : [];
            setReviewSpecs(null);
            setReviewTarget(null);
            executeTarget(target, specs, retranslateCaseIds);
          }}
        />
      )}
    </div>
  );
}
