import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet, apiDelete } from "../api/client";
import { Trash2 } from "lucide-react";

interface SuiteResp {
  name: string;
  base_url: string;
  cases?: unknown[];
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

  useEffect(() => {
    apiGet<SuiteResp>(`/suites/${id}`).then(setSuite).catch(() => {});
  }, [id]);

  async function remove() {
    if (!window.confirm("确认删除此套件？此操作不可恢复，将一并删除其用例与执行记录。"))
      return;
    try {
      await apiDelete(`/suites/${id}`);
      navigate("/suites");
    } catch (e) {
      alert("删除失败: " + (e instanceof Error ? e.message : String(e)));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-surface-900">设置</h1>
        <p className="text-sm text-gray-500 mt-1">套件基本信息与管理操作。</p>
      </div>

      {/* Info card */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden mb-6">
        <Row label="名称" value={suite?.name ?? "—"} />
        <Row
          label="Base URL"
          value={suite?.base_url || <span className="text-gray-300">未设置</span>}
        />
        <Row label="用例数" value={suite?.cases?.length ?? 0} />
      </div>

      {/* Danger zone */}
      <div className="border border-red-200 rounded-lg overflow-hidden">
        <div className="px-5 py-3 bg-red-50/60 border-b border-red-200">
          <h3 className="text-sm font-medium text-red-700">危险操作</h3>
        </div>
        <div className="flex items-center justify-between px-5 py-4">
          <div>
            <p className="text-sm font-medium text-surface-900">删除套件</p>
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
