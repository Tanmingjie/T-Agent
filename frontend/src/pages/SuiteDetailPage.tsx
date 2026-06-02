import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { apiGet, apiPost } from "../api/client";

interface Case {
  id: string;
  name: string;
  steps: string[];
  preconditions: string[];
  expected: string[];
}

interface Run {
  id: string;
  status: string;
  passed_cases: number;
  failed_cases: number;
  total_cases: number;
  started_at: number;
}

export default function SuiteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [suite, setSuite] = useState<{ name: string; base_url: string; cases: Case[]; runs: Run[] } | null>(null);
  const [uploading, setUploading] = useState(false);

  async function load() {
    setSuite(await apiGet(`/suites/${id}`));
  }
  useEffect(() => { load(); }, [id]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      await apiPost(`/suites/${id}/upload`, fd);
      load();
    } finally {
      setUploading(false);
    }
  }

  function handleRun() {
    navigate(`/suites/${id}/run`);
  }

  if (!suite) return <p>加载中...</p>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <button onClick={() => navigate("/suites")} className="text-sm text-gray-500 hover:underline mb-1">
            ← 返回
          </button>
          <h2 className="text-2xl font-bold">{suite.name}</h2>
        </div>
        <div className="flex gap-3">
          <label className={`px-4 py-2 rounded cursor-pointer ${uploading ? "bg-gray-400" : "bg-cyan-600"} text-white`}>
            {uploading ? "解析中..." : "上传 Excel"}
            <input type="file" accept=".xlsx" className="hidden" onChange={handleUpload} disabled={uploading} />
          </label>
          <button
            onClick={handleRun}
            className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
            disabled={suite.cases.length === 0}
          >
            执行
          </button>
        </div>
      </div>

      {/* Cases table */}
      <section className="mb-8">
        <h3 className="text-lg font-semibold mb-3">用例列表 ({suite.cases.length})</h3>
        {suite.cases.length === 0 ? (
          <p className="text-gray-500">尚未上传用例。上传 Excel 文件开始。</p>
        ) : (
          <div className="bg-white border rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-2">ID</th>
                  <th className="text-left px-4 py-2">名称</th>
                  <th className="text-left px-4 py-2">步骤数</th>
                  <th className="text-left px-4 py-2">预置条件</th>
                </tr>
              </thead>
              <tbody>
                {suite.cases.map((c) => (
                  <tr key={c.id} className="border-t hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-xs">{c.id}</td>
                    <td className="px-4 py-2">{c.name}</td>
                    <td className="px-4 py-2">{c.steps.length}</td>
                    <td className="px-4 py-2">{c.preconditions.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Run history */}
      <section>
        <h3 className="text-lg font-semibold mb-3">执行历史</h3>
        {suite.runs.length === 0 ? (
          <p className="text-gray-500">暂无执行记录。</p>
        ) : (
          <div className="bg-white border rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-2">时间</th>
                  <th className="text-left px-4 py-2">状态</th>
                  <th className="text-left px-4 py-2">结果</th>
                </tr>
              </thead>
              <tbody>
                {suite.runs.map((r) => (
                  <tr
                    key={r.id}
                    className="border-t hover:bg-gray-50 cursor-pointer"
                    onClick={() => navigate(`/suites/${id}/runs/${r.id}`)}
                  >
                    <td className="px-4 py-2">
                      {new Date(r.started_at * 1000).toLocaleString()}
                    </td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs ${
                        r.status === "running" ? "bg-cyan-100 text-cyan-800" :
                        r.status === "completed" ? "bg-green-100 text-green-800" :
                        "bg-red-100 text-red-800"
                      }`}>{r.status}</span>
                    </td>
                    <td className="px-4 py-2">
                      {r.passed_cases}/{r.total_cases} 通过
                      {r.failed_cases > 0 && ` · ${r.failed_cases} 失败`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
