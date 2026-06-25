import { useEffect, useState, useCallback } from "react";
import {
  Plus,
  Trash2,
  ChevronRight,
  BookMarked,
  Check,
  X,
  Lock,
} from "lucide-react";
import Editor from "@monaco-editor/react";
import { apiGet, apiPut, apiDelete } from "../api/client";
import { getProjectId } from "../lib/session";

interface Skill {
  name: string;
  description: string;
  content: string;
  updated_at?: number;
}

export default function SkillsPage() {
  const pid = getProjectId();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [isNew, setIsNew] = useState(false);
  const [activeName, setActiveName] = useState<string | null>(null);

  const [dName, setDName] = useState("");
  const [dDesc, setDDesc] = useState("");
  const [dContent, setDContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    if (!pid) return;
    try {
      const data = await apiGet<Skill[]>(`/projects/${pid}/skills`);
      setSkills(data);
    } catch {}
  }, [pid]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setPanelOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function openSkill(s: Skill) {
    setActiveName(s.name);
    setIsNew(false);
    setDName(s.name);
    setDDesc(s.description);
    setDContent(s.content);
    setPanelOpen(true);
    setSaved(false);
  }

  function openNew() {
    setActiveName(null);
    setIsNew(true);
    setDName("");
    setDDesc("");
    setDContent("");
    setPanelOpen(true);
    setSaved(false);
  }

  async function save() {
    if (!pid || !dName.trim()) return;
    setSaving(true);
    try {
      await apiPut(`/projects/${pid}/skills/${encodeURIComponent(dName.trim())}`, {
        name: dName.trim(),
        description: dDesc,
        content: dContent,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      if (isNew) {
        setActiveName(dName.trim());
        setIsNew(false);
      }
      await load();
    } catch (e) {
      alert("保存失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSaving(false);
    }
  }

  async function del() {
    if (!pid || isNew || !activeName) return;
    if (!confirm(`删除 Skill「${activeName}」?此操作不可撤销。`)) return;
    try {
      await apiDelete(`/projects/${pid}/skills/${encodeURIComponent(activeName)}`);
      setPanelOpen(false);
      await load();
    } catch (e) {
      alert("删除失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  if (!pid) {
    return (
      <div className="text-sm text-gray-500">
        未指定项目。请通过内网系统进入,或在 URL 加 ?project=&lt;id&gt;。
      </div>
    );
  }

  return (
    <>
      {/* Main content — panel overlays on top, no layout shift */}
      <div>
        {/* Header */}
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-xl font-semibold text-surface-900">业务 Skills</h1>
            <p className="text-sm text-gray-500 mt-1">
              AI 执行时按需加载的项目专属知识。
              <span className="font-medium text-gray-600">简述</span>
              常驻提示供 AI 判断相关性,
              <span className="font-medium text-gray-600">正文</span>
              仅在相关时展开。
            </p>
          </div>
          <button
            onClick={openNew}
            className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-brand-700 transition-colors shrink-0 ml-6"
          >
            <Plus size={15} /> 新建 Skill
          </button>
        </div>

        {skills.length === 0 ? (
          <EmptyState onNew={openNew} />
        ) : (
          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50/70 border-b border-gray-100">
                  <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 uppercase tracking-wide w-52">
                    名称
                  </th>
                  <th className="text-left px-5 py-3 text-xs font-medium text-gray-500 uppercase tracking-wide">
                    简述
                  </th>
                  <th className="w-10 px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {skills.map((s) => (
                  <tr
                    key={s.name}
                    onClick={() => openSkill(s)}
                    className={`cursor-pointer transition-colors hover:bg-gray-50 ${
                      activeName === s.name && panelOpen
                        ? "bg-brand-50/60 hover:bg-brand-50"
                        : ""
                    }`}
                  >
                    <td className="px-5 py-3.5">
                      <span className="font-medium text-surface-900">{s.name}</span>
                    </td>
                    <td className="px-5 py-3.5 max-w-0">
                      {s.description ? (
                        <span className="block truncate text-gray-500">
                          {s.description}
                        </span>
                      ) : (
                        <span className="text-gray-300 italic text-xs">未填写</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5">
                      <ChevronRight size={15} className="text-gray-300" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Backdrop — overlays page so panel sits on top without shifting layout */}
      <div
        onClick={() => setPanelOpen(false)}
        className={`fixed inset-0 bg-black/20 z-40 transition-opacity duration-200 ${
          panelOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      />

      {/* Side panel — fixed, slides in from right, overlays content */}
      <aside
        className={`fixed top-0 right-0 h-screen w-[46vw] min-w-[520px] max-w-[840px] bg-white border-l border-gray-200 shadow-2xl z-50 flex flex-col transform-gpu will-change-transform transition-transform duration-200 ${
          panelOpen ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {/* Panel header */}
        <div className="h-14 px-5 flex items-center justify-between border-b border-gray-200 shrink-0">
          <h2 className="font-semibold text-surface-900 truncate text-[15px]">
            {isNew ? "新建 Skill" : (activeName ?? "—")}
          </h2>
          <button
            onClick={() => setPanelOpen(false)}
            className="p-1.5 rounded-md text-gray-400 hover:text-surface-900 hover:bg-gray-100 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Panel body — definite-height flex column so Monaco's height:100% resolves */}
        <div className="flex-1 min-h-0 p-5 flex flex-col gap-4 overflow-hidden">
            {/* Name */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1.5">
                名称
                {!isNew && (
                  <span className="ml-2 inline-flex items-center gap-1 text-gray-400 font-normal">
                    <Lock size={10} /> 只读
                  </span>
                )}
              </label>
              <input
                value={dName}
                onChange={(e) => setDName(e.target.value)}
                placeholder="my-skill-name"
                disabled={!isNew}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500 disabled:bg-gray-50 disabled:text-gray-500 disabled:cursor-not-allowed"
              />
              {!isNew && (
                <p className="mt-1.5 text-xs text-gray-400">
                  名称不可修改。需重命名请删除后重建。
                </p>
              )}
            </div>

            {/* Description */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1.5">
                简述
                <span className="ml-1.5 text-gray-400 font-normal">
                  — 常驻 AI 提示,供判断是否加载
                </span>
              </label>
              <input
                value={dDesc}
                onChange={(e) => setDDesc(e.target.value)}
                placeholder="如「订单状态流转规则,在结算相关步骤时加载」"
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/30 focus:border-brand-500"
              />
            </div>

            {/* Content — Monaco editor */}
            <div className="flex-1 min-h-0 flex flex-col">
              <label className="block text-xs font-medium text-gray-600 mb-1.5">
                正文
                <span className="ml-1.5 text-gray-400 font-normal">
                  — AI 判断相关时展开的完整业务知识
                </span>
              </label>
              <div className="border border-gray-300 rounded-md overflow-hidden flex-1">
                <Editor
                  key={isNew ? "__new__" : (activeName ?? "__new__")}
                  height="100%"
                  defaultLanguage="markdown"
                  value={dContent}
                  onChange={(v) => setDContent(v ?? "")}
                  theme="vs"
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbers: "on",
                    wordWrap: "on",
                    scrollBeyondLastLine: false,
                    padding: { top: 10, bottom: 10 },
                    fontFamily: "Menlo, Monaco, Consolas, 'Courier New', monospace",
                    renderLineHighlight: "none",
                    overviewRulerBorder: false,
                    hideCursorInOverviewRuler: true,
                  }}
                />
              </div>
              <p className="mt-1.5 text-xs text-gray-400 shrink-0">
                支持 Markdown。写业务规则、操作约束、术语表等执行期参考知识。
              </p>
            </div>
        </div>

        {/* Panel footer */}
        <div className="shrink-0 px-5 py-4 border-t border-gray-100 flex items-center justify-between">
          <button
            onClick={del}
            disabled={isNew}
            className="inline-flex items-center gap-1.5 text-sm text-red-500 hover:text-red-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <Trash2 size={14} /> 删除
          </button>
          <div className="flex items-center gap-3">
            {saved && (
              <span className="inline-flex items-center gap-1 text-xs text-brand-600">
                <Check size={12} /> 已保存
              </span>
            )}
            <button
              onClick={save}
              disabled={saving || !dName.trim()}
              className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50 transition-colors"
            >
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="w-12 h-12 rounded-xl bg-gray-100 flex items-center justify-center mb-4">
        <BookMarked size={22} className="text-gray-400" />
      </div>
      <h3 className="text-sm font-medium text-surface-900 mb-1">暂无 Skills</h3>
      <p className="text-xs text-gray-500 mb-6 max-w-xs">
        添加项目业务知识供 AI 执行时按需参考,如「业务术语表」「操作限制规则」「错误处理策略」
      </p>
      <button
        onClick={onNew}
        className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-brand-700 transition-colors"
      >
        <Plus size={15} /> 新建第一条 Skill
      </button>
    </div>
  );
}
