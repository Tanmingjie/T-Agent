// 平台化 T-P11/M2:项目设置页(标签页)——LLM 配置 / HTTP 工具 / Skills。
// 后端 API 见 api/routers/projects.py;凭据(api_key/headers)不返明文。
import { useEffect, useState } from "react";
import { Check, Plug, Trash2, Plus } from "lucide-react";
import { apiGet, apiPut, apiPost, apiDelete } from "../api/client";
import { getProjectId } from "../lib/session";

type Tab = "llm" | "http" | "skills";

export default function ProjectSettingsPage() {
  const pid = getProjectId();
  const [tab, setTab] = useState<Tab>("llm");

  if (!pid) {
    return (
      <div className="text-sm text-gray-500">
        未指定项目。请通过内网系统进入,或在 URL 加 ?project=&lt;id&gt;。
      </div>
    );
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "llm", label: "LLM 配置" },
    { id: "http", label: "HTTP 工具" },
    { id: "skills", label: "Skills" },
  ];

  return (
    <div className="max-w-3xl">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-surface-900">项目设置</h1>
        <p className="text-sm text-gray-500 mt-1">凭据加密存储,界面不回显明文。</p>
      </div>
      <div className="flex gap-1 border-b border-gray-200 mb-5">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm -mb-px border-b-2 ${
              tab === t.id
                ? "border-brand-600 text-brand-700 font-medium"
                : "border-transparent text-gray-500 hover:text-surface-900"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "llm" && <LLMSection pid={pid} />}
      {tab === "http" && <HttpToolsSection pid={pid} />}
      {tab === "skills" && <SkillsSection pid={pid} />}
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-3">
      {children}
    </div>
  );
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
    />
  );
}

// ── LLM ──────────────────────────────────────────────────────

interface LLMConfig {
  model: string;
  api_base: string;
  api_key_masked: string;
  has_key: boolean;
}

function LLMSection({ pid }: { pid: string }) {
  const [cfg, setCfg] = useState<LLMConfig | null>(null);
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);
  const [check, setCheck] = useState("");

  useEffect(() => {
    apiGet<LLMConfig>(`/projects/${pid}/llm-config`)
      .then((c) => {
        setCfg(c);
        setModel(c.model);
        setApiBase(c.api_base);
      })
      .catch(() => {});
  }, [pid]);

  async function save() {
    await apiPut(`/projects/${pid}/llm-config`, {
      model,
      api_base: apiBase,
      api_key: apiKey,
    });
    setApiKey("");
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }
  async function runCheck() {
    setCheck("检测中…");
    try {
      const r = await apiPost<{ ok: boolean; reply?: string; error?: string }>(
        `/projects/${pid}/llm-config/check`,
      );
      setCheck(r.ok ? `✅ ${r.reply ?? "正常"}` : `❌ ${r.error}`);
    } catch (e) {
      setCheck("❌ " + (e instanceof Error ? e.message : String(e)));
    }
  }

  return (
    <Card>
      <label className="block text-xs font-medium text-gray-600">模型(带 provider 前缀)</label>
      <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="openai/xxx" />
      <label className="block text-xs font-medium text-gray-600">API Base</label>
      <Input value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
      <label className="block text-xs font-medium text-gray-600">API Key</label>
      <Input
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder={cfg?.has_key ? cfg.api_key_masked : "未设置"}
      />
      <p className="text-xs text-gray-400">留空不修改;输入新值则替换。</p>
      <div className="flex items-center gap-3 pt-1">
        <button onClick={save} className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm">
          保存
        </button>
        <button
          onClick={runCheck}
          className="inline-flex items-center gap-1.5 border border-gray-300 px-4 py-2 rounded-md text-sm"
        >
          <Plug size={15} /> 自检
        </button>
        {saved && (
          <span className="inline-flex items-center gap-1 text-xs text-brand-600">
            <Check size={13} /> 已保存
          </span>
        )}
      </div>
      {check && <p className="text-sm text-gray-700">{check}</p>}
    </Card>
  );
}

// ── HTTP Tools ───────────────────────────────────────────────

interface HttpTool {
  name: string;
  method: string;
  url: string;
  description: string;
  header_keys: string[];
}

function HttpToolsSection({ pid }: { pid: string }) {
  const [tools, setTools] = useState<HttpTool[]>([]);
  const [name, setName] = useState("");
  const [method, setMethod] = useState("GET");
  const [url, setUrl] = useState("");

  async function load() {
    setTools(await apiGet<HttpTool[]>(`/projects/${pid}/http-tools`));
  }
  useEffect(() => {
    load();
  }, [pid]);

  async function add() {
    if (!name || !url) return;
    await apiPut(`/projects/${pid}/http-tools/${encodeURIComponent(name)}`, {
      name,
      method,
      url,
    });
    setName("");
    setUrl("");
    load();
  }
  async function del(n: string) {
    await apiDelete(`/projects/${pid}/http-tools/${encodeURIComponent(n)}`);
    load();
  }

  return (
    <Card>
      <p className="text-xs text-gray-500">
        受控 HTTP 调用(默认仅内网,防 SSRF)。url/body 支持 {"{arg}"} 占位。
      </p>
      <ToolList items={tools.map((t) => ({ key: t.name, label: `${t.method} ${t.name} → ${t.url}` }))} onDelete={del} />
      <div className="flex gap-2 pt-2">
        <Input placeholder="名称" value={name} onChange={(e) => setName(e.target.value)} />
        <select
          value={method}
          onChange={(e) => setMethod(e.target.value)}
          className="border border-gray-300 rounded-md px-2 text-sm"
        >
          {["GET", "POST", "PUT", "DELETE"].map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
        <Input placeholder="http://内网/api" value={url} onChange={(e) => setUrl(e.target.value)} />
        <AddBtn onClick={add} />
      </div>
    </Card>
  );
}

// ── Skills ───────────────────────────────────────────────────

interface Skill {
  name: string;
  description: string;
  content: string;
}

function SkillsSection({ pid }: { pid: string }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");

  async function load() {
    setSkills(await apiGet<Skill[]>(`/projects/${pid}/skills`));
  }
  useEffect(() => {
    load();
  }, [pid]);

  async function add() {
    if (!name) return;
    await apiPut(`/projects/${pid}/skills/${encodeURIComponent(name)}`, {
      name,
      description,
      content,
    });
    setName("");
    setDescription("");
    setContent("");
    load();
  }
  async function del(n: string) {
    await apiDelete(`/projects/${pid}/skills/${encodeURIComponent(n)}`);
    load();
  }

  return (
    <Card>
      <p className="text-xs text-gray-500">
        项目业务知识(标准 Skill 渐进加载)。<b>简述</b>常驻提示供 AI 判断是否相关;相关时 AI
        自动加载<b>正文</b>。简述写「这条知识讲什么、何时用」,正文写完整业务规则。
      </p>
      <ToolList
        items={skills.map((s) => ({
          key: s.name,
          label: `${s.name}: ${s.description || s.content.slice(0, 50)}`,
        }))}
        onDelete={del}
      />
      <div className="space-y-2 pt-2">
        <Input placeholder="名称" value={name} onChange={(e) => setName(e.target.value)} />
        <Input
          placeholder="简述(常驻提示,供 AI 判断何时加载,如「订单状态流转规则」)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <textarea
          placeholder="正文(AI 判断相关时加载的完整业务知识)"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm h-20"
        />
        <AddBtn onClick={add} label="添加 Skill" />
      </div>
    </Card>
  );
}

// ── 共用小组件 ───────────────────────────────────────────────

function ToolList({
  items,
  onDelete,
}: {
  items: { key: string; label: string }[];
  onDelete: (k: string) => void;
}) {
  if (!items.length)
    return <p className="text-xs text-gray-400 py-2">暂无</p>;
  return (
    <div className="divide-y divide-gray-100 border border-gray-100 rounded-md">
      {items.map((it) => (
        <div key={it.key} className="flex items-center justify-between px-3 py-2">
          <span className="text-sm text-surface-900 truncate">{it.label}</span>
          <button onClick={() => onDelete(it.key)} className="text-red-500 hover:text-red-600">
            <Trash2 size={15} />
          </button>
        </div>
      ))}
    </div>
  );
}

function AddBtn({ onClick, label = "添加" }: { onClick: () => void; label?: string }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 bg-brand-600 text-white px-3 py-2 rounded-md text-sm whitespace-nowrap"
    >
      <Plus size={15} /> {label}
    </button>
  );
}
