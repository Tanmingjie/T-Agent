import { useMemo, useState } from "react";
import { Check, FileCheck2, Plus, Trash2, X } from "lucide-react";

export interface EditablePhase {
  steps: string[];
  expected: string;
}

export interface EditableTestSpec {
  case_id: string;
  name: string;
  base_url: string;
  intent: string;
  preconditions: string[];
  phases: EditablePhase[];
}

interface Props {
  specs: EditableTestSpec[];
  onCancel: () => void;
  onConfirm: (specs: EditableTestSpec[]) => void;
}

export default function SpecReviewDialog({ specs, onCancel, onConfirm }: Props) {
  const [drafts, setDrafts] = useState(() => structuredClone(specs));
  const [activeId, setActiveId] = useState(specs[0]?.case_id ?? "");
  const activeIndex = drafts.findIndex((spec) => spec.case_id === activeId);
  const active = drafts[activeIndex];
  const invalidIds = useMemo(
    () =>
      new Set(
        drafts
          .filter(
            (spec) =>
              spec.phases.length === 0 ||
              spec.phases.some(
                (phase) => phase.steps.length === 0 || !phase.expected.trim(),
              ),
          )
          .map((spec) => spec.case_id),
      ),
    [drafts],
  );

  function updateActive(mutator: (spec: EditableTestSpec) => void) {
    setDrafts((prev) => {
      const next = structuredClone(prev);
      const index = next.findIndex((spec) => spec.case_id === activeId);
      if (index >= 0) mutator(next[index]);
      return next;
    });
  }

  if (!active) return null;

  return (
    <div className="fixed inset-0 z-[70] bg-black/35 flex items-center justify-center p-4">
      <div className="bg-white w-full max-w-6xl h-[min(840px,92vh)] rounded-lg shadow-elevated overflow-hidden flex flex-col">
        <header className="h-16 shrink-0 px-5 border-b border-gray-200 flex items-center justify-between">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-surface-900 flex items-center gap-2">
              <FileCheck2 size={18} className="text-brand-600" />
              确认执行规格
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              检查每个阶段的步骤和预期，确认后将直接交给 Midscene 执行。
            </p>
          </div>
          <button
            onClick={onCancel}
            className="w-8 h-8 grid place-items-center rounded-md text-gray-500 hover:bg-gray-100"
            title="关闭"
          >
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 min-h-0 grid grid-rows-[132px_minmax(0,1fr)] md:grid-rows-1 md:grid-cols-[240px_minmax(0,1fr)]">
          <aside className="border-b md:border-b-0 md:border-r border-gray-200 bg-gray-50/70 overflow-y-auto p-2">
            {drafts.map((spec, index) => (
              <button
                key={spec.case_id}
                onClick={() => setActiveId(spec.case_id)}
                className={`w-full text-left px-3 py-2.5 rounded-md mb-1 border ${
                  spec.case_id === activeId
                    ? "bg-white border-brand-200 shadow-sm"
                    : "border-transparent hover:bg-white"
                }`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-xs text-gray-400">用例 {index + 1}</span>
                  {invalidIds.has(spec.case_id) ? (
                    <span className="text-xs text-red-600">待完善</span>
                  ) : (
                    <Check size={14} className="text-emerald-600" />
                  )}
                </span>
                <span className="block text-sm font-medium text-surface-900 mt-1 truncate">
                  {spec.name}
                </span>
                <span className="block text-xs text-gray-400 mt-1">
                  {spec.phases.length} 个阶段
                </span>
              </button>
            ))}
          </aside>

          <main className="overflow-y-auto px-6 py-5">
            <div className="max-w-3xl mx-auto space-y-5">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1.5">
                  整体意图
                </label>
                <textarea
                  value={active.intent}
                  onChange={(event) =>
                    updateActive((spec) => {
                      spec.intent = event.target.value;
                    })
                  }
                  rows={2}
                  className="w-full resize-y rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-200 focus:border-brand-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1.5">
                  前置条件 <span className="font-normal text-gray-400">每行一条</span>
                </label>
                <textarea
                  value={active.preconditions.join("\n")}
                  onChange={(event) =>
                    updateActive((spec) => {
                      spec.preconditions = event.target.value
                        .split("\n")
                        .map((line) => line.trim())
                        .filter(Boolean);
                    })
                  }
                  rows={Math.max(2, active.preconditions.length)}
                  className="w-full resize-y rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-200 focus:border-brand-500"
                />
              </div>

              <div className="flex items-center justify-between border-b border-gray-200 pb-2">
                <h3 className="text-sm font-semibold text-surface-900">执行阶段</h3>
                <button
                  onClick={() =>
                    updateActive((spec) => {
                      spec.phases.push({ steps: [""], expected: "" });
                    })
                  }
                  className="inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:text-brand-800"
                >
                  <Plus size={14} /> 添加阶段
                </button>
              </div>

              {active.phases.map((phase, phaseIndex) => (
                <section
                  key={phaseIndex}
                  className="rounded-md border border-gray-200 bg-white"
                >
                  <div className="h-10 px-3 border-b border-gray-100 bg-gray-50 flex items-center justify-between">
                    <span className="text-sm font-medium text-surface-900">
                      阶段 {phaseIndex + 1}
                    </span>
                    <button
                      onClick={() =>
                        updateActive((spec) => {
                          spec.phases.splice(phaseIndex, 1);
                        })
                      }
                      className="w-7 h-7 grid place-items-center rounded text-gray-400 hover:text-red-600 hover:bg-red-50"
                      title="删除阶段"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div className="p-3 space-y-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-500 mb-1.5">
                        执行步骤 <span className="font-normal">每行一步</span>
                      </label>
                      <textarea
                        value={phase.steps.join("\n")}
                        onChange={(event) =>
                          updateActive((spec) => {
                            spec.phases[phaseIndex].steps = event.target.value
                              .split("\n")
                              .map((line) => line.trim())
                              .filter(Boolean);
                          })
                        }
                        rows={Math.max(3, phase.steps.length)}
                        className="w-full resize-y rounded-md border border-gray-300 px-3 py-2 text-sm leading-6 focus:outline-none focus:ring-2 focus:ring-brand-200 focus:border-brand-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-500 mb-1.5">
                        阶段预期
                      </label>
                      <textarea
                        value={phase.expected}
                        onChange={(event) =>
                          updateActive((spec) => {
                            spec.phases[phaseIndex].expected = event.target.value;
                          })
                        }
                        rows={2}
                        className={`w-full resize-y rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                          phase.expected.trim()
                            ? "border-gray-300 focus:ring-brand-200 focus:border-brand-500"
                            : "border-red-300 focus:ring-red-100 focus:border-red-500"
                        }`}
                      />
                    </div>
                  </div>
                </section>
              ))}
            </div>
          </main>
        </div>

        <footer className="h-16 shrink-0 px-5 border-t border-gray-200 flex items-center justify-between">
          <span className="text-xs text-gray-500">
            {invalidIds.size === 0
              ? `已检查 ${drafts.length} 条用例`
              : `还有 ${invalidIds.size} 条用例存在空步骤或空预期`}
          </span>
          <div className="flex gap-2">
            <button
              onClick={onCancel}
              className="px-3.5 py-2 rounded-md text-sm font-medium border border-gray-300 text-gray-700 hover:bg-gray-50"
            >
              返回
            </button>
            <button
              disabled={invalidIds.size > 0}
              onClick={() => onConfirm(drafts)}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-md text-sm font-medium bg-brand-600 text-white hover:bg-brand-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
            >
              <Check size={15} /> 确认并执行
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
