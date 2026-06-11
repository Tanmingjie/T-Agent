// 平台化 T-P11:项目设置页 —— 项目级 LLM 配置(对接 T-P06)。
// api_key 回显掩码、不返明文;留空/掩码提交则保留原 key。连通自检。
import { useEffect, useState } from "react";
import { Check, Plug } from "lucide-react";
import { apiGet, apiPut, apiPost } from "../api/client";
import { getProjectId } from "../lib/session";

interface LLMConfig {
  project_id: string;
  model: string;
  api_base: string;
  api_key_masked: string;
  has_key: boolean;
  temperature: number;
}

export default function ProjectSettingsPage() {
  const pid = getProjectId();
  const [cfg, setCfg] = useState<LLMConfig | null>(null);
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState(""); // 留空=不改;输入新值=替换
  const [saved, setSaved] = useState(false);
  const [check, setCheck] = useState<string>("");

  useEffect(() => {
    if (!pid) return;
    apiGet<LLMConfig>(`/projects/${pid}/llm-config`)
      .then((c) => {
        setCfg(c);
        setModel(c.model);
        setApiBase(c.api_base);
      })
      .catch(() => {});
  }, [pid]);

  if (!pid) {
    return (
      <div className="text-sm text-gray-500">
        请先在左下角选择一个项目。
      </div>
    );
  }

  async function save() {
    try {
      const body = {
        model,
        api_base: apiBase,
        api_key: apiKey, // 空 → 后端保留原 key
        temperature: cfg?.temperature ?? 0,
      };
      const next = await apiPut<LLMConfig>(`/projects/${pid}/llm-config`, body);
      setCfg(next);
      setApiKey("");
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      alert("保存失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function runCheck() {
    setCheck("检测中…");
    try {
      const r = await apiPost<{ ok: boolean; reply?: string; error?: string }>(
        `/projects/${pid}/llm-config/check`,
      );
      setCheck(r.ok ? `✅ 连通正常:${r.reply ?? ""}` : `❌ ${r.error}`);
    } catch (e) {
      setCheck("❌ " + (e instanceof Error ? e.message : String(e)));
    }
  }

  return (
    <div className="max-w-2xl">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">项目设置</h1>
        <p className="text-sm text-gray-500 mt-1">
          项目级 LLM 配置。api_key 加密存储,仅显示尾号。
        </p>
      </div>

      <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
        <Field label="模型(带 provider 前缀,如 openai/xxx)">
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="openai/deepseek-v4-flash"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </Field>
        <Field label="API Base">
          <input
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder="https://api.example.com/v1"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </Field>
        <Field label="API Key">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={cfg?.has_key ? cfg.api_key_masked : "未设置"}
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
          <p className="text-xs text-gray-400 mt-1">
            留空不修改;输入新值则替换。
          </p>
        </Field>

        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={save}
            className="inline-flex items-center gap-1.5 bg-brand-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-brand-700"
          >
            保存
          </button>
          <button
            onClick={runCheck}
            className="inline-flex items-center gap-1.5 border border-gray-300 px-4 py-2 rounded-md text-sm hover:bg-gray-50"
          >
            <Plug size={15} /> 连通自检
          </button>
          {saved && (
            <span className="inline-flex items-center gap-1 text-xs text-brand-600">
              <Check size={13} /> 已保存
            </span>
          )}
        </div>
        {check && <p className="text-sm text-gray-700">{check}</p>}
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}
