import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiGet, apiPost, apiDelete } from "../api/client";
import { Plus, Trash2, ExternalLink, Layers, Play } from "lucide-react";

interface Suite {
  id: string;
  name: string;
  base_url: string;
}

export default function SuiteListPage() {
  const [suites, setSuites] = useState<Suite[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function load() {
    try { setSuites(await apiGet<Suite[]>("/suites")); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  useEffect(() => { load(); }, []);

  async function create() {
    try {
      await apiPost("/suites", { name, base_url: baseUrl });
      setShowCreate(false); setName(""); setBaseUrl(""); load();
    } catch (e) { alert("创建失败: " + (e instanceof Error ? e.message : String(e))); }
  }

  async function remove(id: string) {
    if (!window.confirm("确认删除此 Suite？此操作不可恢复。")) return;
    try { await apiDelete(`/suites/${id}`); load(); }
    catch (e) { alert("删除失败: " + (e instanceof Error ? e.message : String(e))); }
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-surface-900">Suites</h1>
          <p className="text-sm text-gray-500 mt-1">管理你的测试 Suite 并执行自动化测试</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-2 bg-brand-600 text-white px-4 py-2.5 rounded-lg text-sm font-medium hover:bg-brand-700 transition-colors shadow-card"
        >
          <Plus size={16} /> 新建 Suite
        </button>
      </div>

      {error && (
        <div className="mb-6 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">{error}</div>
      )}

      {/* Create form */}
      {showCreate && (
        <div className="mb-6 p-5 bg-white border border-gray-200 rounded-xl shadow-card">
          <h3 className="font-semibold text-surface-900 mb-3">新建 Suite</h3>
          <input
            className="w-full border border-gray-300 px-3 py-2.5 rounded-lg mb-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
            placeholder="Suite 名称"
            value={name} onChange={(e) => setName(e.target.value)}
          />
          <input
            className="w-full border border-gray-300 px-3 py-2.5 rounded-lg mb-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
            placeholder="Base URL (e.g. https://example.com)"
            value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
          />
          <div className="flex gap-2">
            <button onClick={create}
              className="bg-brand-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-brand-700 transition-colors">
              创建
            </button>
            <button onClick={() => setShowCreate(false)}
              className="border border-gray-300 px-4 py-2 rounded-lg text-sm hover:bg-gray-50 transition-colors">
              取消
            </button>
          </div>
        </div>
      )}

      {/* Stats bar */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-card">
          <p className="text-2xl font-bold text-surface-900">{suites.length}</p>
          <p className="text-xs text-gray-500 mt-0.5">Suite 总数</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-card">
          <p className="text-2xl font-bold text-brand-600">—</p>
          <p className="text-xs text-gray-500 mt-0.5">最近执行</p>
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-card">
          <p className="text-2xl font-bold text-emerald-600">—</p>
          <p className="text-xs text-gray-500 mt-0.5">总通过率</p>
        </div>
      </div>

      {/* Empty state */}
      {suites.length === 0 && !showCreate && (
        <div className="bg-white border border-dashed border-gray-300 rounded-xl py-16 text-center">
          <Layers size={40} className="mx-auto text-gray-300 mb-3" />
          <p className="text-gray-500 text-sm mb-1">还没有 Suite</p>
          <p className="text-xs text-gray-400">点击"新建 Suite"创建你的第一个测试 Suite</p>
        </div>
      )}

      {/* Suite cards */}
      <div className="grid gap-3">
        {suites.map((s) => (
          <div
            key={s.id}
            className="bg-white border border-gray-200 rounded-xl p-5 flex items-center justify-between hover:border-brand-200 hover:shadow-elevated transition-all cursor-pointer group"
            onClick={() => navigate(`/suites/${s.id}`)}
          >
            <div className="flex items-center gap-4">
              <div className="w-10 h-10 rounded-lg bg-brand-50 flex items-center justify-center shrink-0">
                <Layers size={20} className="text-brand-600" />
              </div>
              <div>
                <h3 className="font-semibold text-surface-900 group-hover:text-brand-700 transition-colors">{s.name}</h3>
                <p className="text-sm text-gray-500">{s.base_url || "未设置 Base URL"}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={(e) => { e.stopPropagation(); navigate(`/suites/${s.id}`); }}
                className="p-2 rounded-lg text-gray-400 hover:text-brand-600 hover:bg-brand-50 transition-colors"
                title="打开"
              >
                <ExternalLink size={16} />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); navigate(`/suites/${s.id}/run`); }}
                className="p-2 rounded-lg text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 transition-colors"
                title="执行"
              >
                <Play size={16} />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); remove(s.id); }}
                className="p-2 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                title="删除"
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
