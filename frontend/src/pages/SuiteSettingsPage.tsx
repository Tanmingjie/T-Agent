import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet, apiPut, apiPost, apiDelete } from "../api/client";
import { Trash2, Check, Upload, X } from "lucide-react";

interface SuiteResp {
  name: string;
  base_url: string;
  cases?: { id: string; name: string }[];
}

interface SuiteSettings {
  permission_mode: string;
  parallelism: number;
  login_setup_case_id: string | null;
  auth_state?: {
    uploaded: boolean;
    size: number;
    updated_at: number | null;
  };
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between px-5 py-3.5 border-b border-gray-100 last:border-0">
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-sm text-surface-900 font-medium">{value}</span>
    </div>
  );
}

export default function SuiteSettingsPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [suite, setSuite] = useState<SuiteResp | null>(null);
  const [settings, setSettings] = useState<SuiteSettings | null>(null);
  const [saved, setSaved] = useState(false);
  const [authBusy, setAuthBusy] = useState(false);

  useEffect(() => {
    apiGet<SuiteResp>(`/suites/${id}`)
      .then(setSuite)
      .catch(() => {});
    apiGet<SuiteSettings>(`/suites/${id}/settings`)
      .then(setSettings)
      .catch(() => {});
  }, [id]);

  async function saveParallelism(n: number) {
    if (!settings) return;
    const next = { ...settings, parallelism: Math.max(1, n) };
    setSettings(next);
    try {
      await apiPut(`/suites/${id}/settings`, next);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      alert("保存失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function saveLoginSetupCase(caseId: string) {
    if (!settings) return;
    const next = { ...settings, login_setup_case_id: caseId || null };
    setSettings(next);
    try {
      await apiPut(`/suites/${id}/settings`, next);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      alert("保存失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  async function uploadAuthState(file: File | null) {
    if (!settings || !file) return;
    setAuthBusy(true);
    try {
      const storageState = JSON.parse(await file.text());
      const resp = await apiPost<{ auth_state: SuiteSettings["auth_state"] }>(
        `/suites/${id}/auth-state`,
        { storage_state: storageState },
      );
      setSettings({ ...settings, auth_state: resp.auth_state });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      alert("上传失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setAuthBusy(false);
    }
  }

  async function clearAuthState() {
    if (!settings) return;
    setAuthBusy(true);
    try {
      const resp = await apiDelete(`/suites/${id}/auth-state`);
      setSettings({
        ...settings,
        auth_state: { uploaded: false, size: 0, updated_at: null },
      });
      void resp;
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      alert("清除失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setAuthBusy(false);
    }
  }

  async function remove() {
    if (
      !window.confirm(
        "确认删除此测试任务？此操作不可恢复，将一并删除其用例与执行记录。",
      )
    )
      return;
    try {
      await apiDelete(`/suites/${id}`);
      navigate("/tasks");
    } catch (e) {
      alert("删除失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  const authState = settings?.auth_state;

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">设置</h1>
        <p className="text-sm text-gray-500 mt-1">测试任务基本信息与管理操作。</p>
      </div>

      {/* Info card */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden mb-6">
        <Row label="名称" value={suite?.name ?? "—"} />
        <Row
          label="Base URL"
          value={
            suite?.base_url || <span className="text-gray-300">未设置</span>
          }
        />
        <Row label="用例数" value={suite?.cases?.length ?? 0} />
      </div>

      {/* Execution settings */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden mb-6">
        <div className="px-5 py-3 border-b border-gray-100">
          <h3 className="text-sm font-medium text-surface-900">执行设置</h3>
        </div>
        <div className="flex items-center justify-between px-5 py-4">
          <div>
            <p className="text-sm font-medium text-surface-900">并发执行数</p>
            <p className="text-xs text-gray-500 mt-0.5">
              同时并行执行的用例数(每条独立浏览器)。1 =
              串行;增大更快但更吃内存。
            </p>
          </div>
          <div className="flex items-center gap-2">
            {saved && (
              <span className="inline-flex items-center gap-1 text-xs text-brand-600">
                <Check size={13} /> 已保存
              </span>
            )}
            <input
              type="number"
              min={1}
              max={8}
              value={settings?.parallelism ?? 1}
              onChange={(e) =>
                saveParallelism(parseInt(e.target.value, 10) || 1)
              }
              className="w-20 border border-gray-300 rounded-md px-3 py-1.5 text-sm text-right focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500"
            />
          </div>
        </div>
        <div className="flex items-center justify-between px-5 py-4 border-t border-gray-100">
          <div>
            <p className="text-sm font-medium text-surface-900">登录准备用例</p>
            <p className="text-xs text-gray-500 mt-0.5">
              每次执行先运行一次，后续用例复用本次登录状态。
            </p>
          </div>
          <select
            value={settings?.login_setup_case_id ?? ""}
            onChange={(e) => saveLoginSetupCase(e.target.value)}
            disabled={!settings}
            className="w-64 border border-gray-300 rounded-md px-3 py-1.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-500/40 focus:border-brand-500 disabled:opacity-50"
          >
            <option value="">不使用</option>
            {(suite?.cases ?? []).map((testCase) => (
              <option key={testCase.id} value={testCase.id}>
                {testCase.name} ({testCase.id})
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center justify-between px-5 py-4 border-t border-gray-100">
          <div>
            <p className="text-sm font-medium text-surface-900">上传登录态</p>
            <p className="text-xs text-gray-500 mt-0.5">
              已上传时执行会直接加载 storageState，并跳过登录准备用例。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`text-xs ${
                authState?.uploaded
                  ? "text-brand-700"
                  : "text-gray-400"
              }`}
            >
              {authState?.uploaded
                ? `已上传 ${Math.max(1, Math.round((authState.size || 0) / 1024))} KB`
                : "未上传"}
            </span>
            <label className="inline-flex items-center gap-1.5 border border-gray-300 text-surface-700 px-3 py-1.5 rounded-md text-sm font-medium hover:bg-gray-50 transition-colors cursor-pointer">
              <Upload size={15} /> 上传
              <input
                type="file"
                accept="application/json,.json"
                className="hidden"
                disabled={authBusy || !settings}
                onChange={(e) => {
                  void uploadAuthState(e.target.files?.[0] ?? null);
                  e.currentTarget.value = "";
                }}
              />
            </label>
            {authState?.uploaded && (
              <button
                type="button"
                onClick={clearAuthState}
                disabled={authBusy}
                className="inline-flex items-center gap-1.5 border border-gray-300 text-gray-600 px-3 py-1.5 rounded-md text-sm font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
              >
                <X size={15} /> 清除
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Danger zone */}
      <div className="border border-red-200 rounded-lg overflow-hidden">
        <div className="px-5 py-3 bg-red-50/60 border-b border-red-200">
          <h3 className="text-sm font-medium text-red-700">危险操作</h3>
        </div>
        <div className="flex items-center justify-between px-5 py-4">
          <div>
            <p className="text-sm font-medium text-surface-900">删除测试任务</p>
            <p className="text-xs text-gray-500 mt-0.5">
              一并删除其用例与执行记录，不可恢复。
            </p>
          </div>
          <button
            onClick={remove}
            className="inline-flex items-center gap-1.5 border border-red-300 text-red-600 px-3.5 py-2 rounded-md text-sm font-medium hover:bg-red-50 transition-colors"
          >
            <Trash2 size={16} /> 删除
          </button>
        </div>
      </div>
    </div>
  );
}
