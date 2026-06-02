import { useEffect, useState } from "react";
import { apiGet, apiPost } from "../api/client";

interface Vocab {
  url_pattern: string;
  page_title: string;
  login_role: string;
  vocabulary: Record<string, { role: string; name: string; confidence: number }>;
}

export default function VocabularyPage() {
  const [items, setItems] = useState<Vocab[]>([]);
  const [query, setQuery] = useState("");
  const [scanning, setScanning] = useState(false);

  async function load() {
    const r = await apiGet<{ items: Vocab[] }>(`/vocabulary?query=${encodeURIComponent(query)}`);
    setItems(r.items);
  }
  useEffect(() => { load(); }, [query]);

  async function scan() {
    setScanning(true);
    try {
      await apiPost("/vocabulary/scan");
      load();
    } finally {
      setScanning(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold">Page Intelligence 词汇表</h2>
        <button onClick={scan} disabled={scanning}
          className="bg-cyan-600 text-white px-4 py-2 rounded hover:bg-cyan-700 disabled:opacity-50">
          {scanning ? "扫描中..." : "扫描页面"}
        </button>
      </div>

      <div className="mb-4">
        <input
          className="border px-3 py-2 rounded w-64 text-sm"
          placeholder="搜索 URL 或页面标题..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="text-sm text-gray-500 ml-3">共 {items.length} 条</span>
      </div>

      {items.length === 0 ? (
        <p className="text-gray-500 text-center py-20">暂无词汇表数据。点击"扫描页面"开始。</p>
      ) : (
        <div className="bg-white border rounded overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-4 py-2">页面路径</th>
                <th className="text-left px-4 py-2">页面标题</th>
                <th className="text-left px-4 py-2">登录角色</th>
                <th className="text-left px-4 py-2">词汇数</th>
              </tr>
            </thead>
            <tbody>
              {items.map((v, i) => (
                <tr key={i} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs">{v.url_pattern}</td>
                  <td className="px-4 py-2">{v.page_title}</td>
                  <td className="px-4 py-2">{v.login_role}</td>
                  <td className="px-4 py-2">{Object.keys(v.vocabulary).length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
