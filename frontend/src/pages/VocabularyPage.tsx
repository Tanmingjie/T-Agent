import { Fragment, useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost, apiPut } from "../api/client";

/** 词条:业务词 → 页面真实元素。selector(CSS)最稳健,优先于 role+name。 */
interface VocabEntry {
  role?: string;
  name?: string;
  selector?: string;
  confidence?: number;
  source?: string;
}

interface Vocab {
  url_pattern: string;
  page_title: string;
  login_role: string;
  vocabulary: Record<string, VocabEntry>;
}

const EMPTY_PAGE: Vocab = {
  url_pattern: "",
  page_title: "",
  login_role: "",
  vocabulary: {},
};

export default function VocabularyPage() {
  const [items, setItems] = useState<Vocab[]>([]);
  const [query, setQuery] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [newPage, setNewPage] = useState<Vocab | null>(null);

  function keyOf(v: Vocab) {
    return `${v.url_pattern}|${v.page_title}|${v.login_role}`;
  }

  async function load() {
    const r = await apiGet<{ items: Vocab[] }>(
      `/vocabulary?query=${encodeURIComponent(query)}`,
    );
    setItems(r.items.map((v) => ({ ...v, vocabulary: v.vocabulary || {} })));
  }
  useEffect(() => {
    load();
  }, [query]);

  async function scan() {
    setScanning(true);
    setScanMsg(null);
    try {
      const r = await apiPost<{ message?: string }>("/vocabulary/scan");
      await load();
      setScanMsg(r.message ?? "已触发。词汇表在执行 Suite 时自动增量更新。");
    } catch (e) {
      setScanMsg(`扫描请求失败:${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setScanning(false);
    }
  }

  /** 保存整页词汇表(API 按 url_pattern+title+role 为键 upsert)。 */
  async function savePage(v: Vocab) {
    await apiPut("/vocabulary/0", {
      url_pattern: v.url_pattern,
      page_title: v.page_title,
      login_role: v.login_role,
      vocabulary: v.vocabulary,
      action_map: [],
    });
    await load();
  }

  async function deletePage(v: Vocab) {
    if (!confirm(`删除页面词汇表「${v.page_title || v.url_pattern}」?`)) return;
    const qs = new URLSearchParams({
      url_pattern: v.url_pattern,
      page_title: v.page_title,
      login_role: v.login_role,
    });
    await apiDelete(`/vocabulary/0?${qs.toString()}`);
    setExpanded(null);
    await load();
  }

  async function createPage() {
    if (!newPage || !newPage.url_pattern.trim()) {
      alert("请填写 URL 路径(url_pattern)");
      return;
    }
    await savePage(newPage);
    setNewPage(null);
    setExpanded(keyOf(newPage));
  }

  return (
    <div>
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-surface-900">词汇表</h1>
          <p className="text-sm text-gray-500 mt-1">
            维护业务词 → 页面元素的映射，供断言/自愈跨语言解析目标。
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setNewPage({ ...EMPTY_PAGE })}
            className="border border-gray-300 text-gray-700 px-3.5 py-2 rounded-md text-sm font-medium hover:bg-gray-50 transition-colors"
          >
            新建词汇表
          </button>
          <button
            onClick={scan}
            disabled={scanning}
            className="bg-brand-600 text-white px-3.5 py-2 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50 transition-colors"
          >
            {scanning ? "扫描中…" : "扫描页面"}
          </button>
        </div>
      </div>

      {scanMsg && (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          <span className="mt-0.5">ℹ️</span>
          <span className="flex-1">{scanMsg}</span>
          <button
            onClick={() => setScanMsg(null)}
            className="text-blue-400 hover:text-blue-600"
          >
            ✕
          </button>
        </div>
      )}

      <div className="flex items-center gap-2 mb-3">
        <input
          className="w-64 border border-gray-300 rounded-md px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
          placeholder="搜索 URL 或页面标题…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="text-xs text-gray-400 ml-auto">共 {items.length} 条</span>
      </div>

      {newPage && (
        <NewPageForm
          page={newPage}
          onChange={setNewPage}
          onCancel={() => setNewPage(null)}
          onCreate={createPage}
        />
      )}

      {items.length === 0 ? (
        <div className="bg-white border border-gray-200 rounded-lg py-16 text-center">
          <p className="text-gray-500 text-sm">
            暂无词汇表数据。点击"新建词汇表"手动维护，或"扫描页面"自动提炼。
          </p>
        </div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500">
                <th className="px-5 py-3 w-8"></th>
                <th className="px-5 py-3 font-medium">页面路径</th>
                <th className="px-5 py-3 font-medium">页面标题</th>
                <th className="px-5 py-3 font-medium">登录角色</th>
                <th className="px-5 py-3 font-medium w-20">词汇数</th>
                <th className="px-5 py-3 w-16"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((v) => {
                const k = keyOf(v);
                const open = expanded === k;
                return (
                  <Fragment key={k}>
                    <tr
                      className="border-b border-gray-100 last:border-0 hover:bg-gray-50/70 cursor-pointer transition-colors"
                      onClick={() => setExpanded(open ? null : k)}
                    >
                      <td className="px-5 py-3 text-gray-400">{open ? "▾" : "▸"}</td>
                      <td className="px-5 py-3 font-mono text-xs text-gray-600">{v.url_pattern}</td>
                      <td className="px-5 py-3 text-surface-900">{v.page_title}</td>
                      <td className="px-5 py-3 text-gray-500">{v.login_role || <span className="text-gray-300">(任意)</span>}</td>
                      <td className="px-5 py-3 text-gray-500">{Object.keys(v.vocabulary).length}</td>
                      <td className="px-5 py-3 text-right">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            deletePage(v);
                          }}
                          className="text-red-600 hover:text-red-700 text-xs"
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr>
                        <td colSpan={6} className="bg-gray-50/60 px-5 py-3">
                          <TermEditor page={v} onSave={savePage} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function NewPageForm({
  page,
  onChange,
  onCancel,
  onCreate,
}: {
  page: Vocab;
  onChange: (v: Vocab) => void;
  onCancel: () => void;
  onCreate: () => void;
}) {
  return (
    <div className="bg-white border rounded p-4 mb-4">
      <h3 className="font-semibold mb-3">新建页面词汇表</h3>
      <div className="grid grid-cols-3 gap-3">
        <label className="text-xs text-gray-600">
          URL 路径(支持 /order/&#123;id&#125;)
          <input
            className="border px-2 py-1 rounded w-full mt-1 text-sm font-mono"
            value={page.url_pattern}
            onChange={(e) => onChange({ ...page, url_pattern: e.target.value })}
            placeholder="/inventory"
          />
        </label>
        <label className="text-xs text-gray-600">
          页面标题(留空=任意)
          <input
            className="border px-2 py-1 rounded w-full mt-1 text-sm"
            value={page.page_title}
            onChange={(e) => onChange({ ...page, page_title: e.target.value })}
            placeholder="Swag Labs"
          />
        </label>
        <label className="text-xs text-gray-600">
          登录角色(留空=任意)
          <input
            className="border px-2 py-1 rounded w-full mt-1 text-sm"
            value={page.login_role}
            onChange={(e) => onChange({ ...page, login_role: e.target.value })}
            placeholder="standard_user"
          />
        </label>
      </div>
      <div className="flex gap-2 mt-3">
        <button
          onClick={onCreate}
          className="bg-brand-600 text-white px-3.5 py-1.5 rounded-md text-sm font-medium hover:bg-brand-700 transition-colors"
        >
          创建
        </button>
        <button onClick={onCancel} className="text-gray-600 px-3 py-1.5 text-sm">
          取消
        </button>
      </div>
    </div>
  );
}

const EMPTY_TERM = { term: "", role: "", name: "", selector: "", confidence: "" };

function TermEditor({ page, onSave }: { page: Vocab; onSave: (v: Vocab) => Promise<void> }) {
  const [draft, setDraft] = useState({ ...EMPTY_TERM });
  const entries = Object.entries(page.vocabulary);

  function mutate(next: Record<string, VocabEntry>) {
    return onSave({ ...page, vocabulary: next });
  }

  async function addTerm() {
    const t = draft.term.trim();
    if (!t) return;
    if (!draft.selector.trim() && !draft.name.trim()) {
      alert("至少填 selector 或 name 之一");
      return;
    }
    const entry: VocabEntry = { source: "manual" };
    if (draft.role.trim()) entry.role = draft.role.trim();
    if (draft.name.trim()) entry.name = draft.name.trim();
    if (draft.selector.trim()) entry.selector = draft.selector.trim();
    if (draft.confidence.trim()) entry.confidence = Number(draft.confidence);
    await mutate({ ...page.vocabulary, [t]: entry });
    setDraft({ ...EMPTY_TERM });
  }

  async function deleteTerm(term: string) {
    const next = { ...page.vocabulary };
    delete next[term];
    await mutate(next);
  }

  return (
    <div>
      {entries.length === 0 ? (
        <p className="text-gray-400 text-xs mb-2">暂无词条。在下方添加业务词 → 页面元素映射。</p>
      ) : (
        <table className="w-full text-xs mb-3">
          <thead className="text-gray-500">
            <tr>
              <th className="text-left py-1">业务词</th>
              <th className="text-left py-1">selector (CSS)</th>
              <th className="text-left py-1">role</th>
              <th className="text-left py-1">name</th>
              <th className="text-left py-1">来源</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([term, e]) => (
              <tr key={term} className="border-t border-gray-200">
                <td className="py-1 font-medium">{term}</td>
                <td className="py-1 font-mono text-brand-700">{e.selector || "—"}</td>
                <td className="py-1">{e.role || "—"}</td>
                <td className="py-1">{e.name || "—"}</td>
                <td className="py-1 text-gray-500">{e.source || "—"}</td>
                <td className="py-1 text-right">
                  <button
                    onClick={() => deleteTerm(term)}
                    className="text-red-600 hover:underline"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <Field label="业务词" value={draft.term} onChange={(v) => setDraft({ ...draft, term: v })} placeholder="购物车图标" />
        <Field label="selector" mono value={draft.selector} onChange={(v) => setDraft({ ...draft, selector: v })} placeholder=".shopping_cart_badge" />
        <Field label="role" value={draft.role} onChange={(v) => setDraft({ ...draft, role: v })} placeholder="link" />
        <Field label="name" value={draft.name} onChange={(v) => setDraft({ ...draft, name: v })} placeholder="1" />
        <Field label="conf" value={draft.confidence} onChange={(v) => setDraft({ ...draft, confidence: v })} placeholder="0.9" width="w-16" />
        <button
          onClick={addTerm}
          className="bg-brand-600 text-white px-3.5 py-1.5 rounded-md text-xs font-medium hover:bg-brand-700 transition-colors"
        >
          添加词条
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  mono,
  width = "w-36",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
  width?: string;
}) {
  return (
    <label className="text-[11px] text-gray-500">
      {label}
      <input
        className={`border px-2 py-1 rounded ${width} mt-0.5 text-xs block ${mono ? "font-mono" : ""}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}
