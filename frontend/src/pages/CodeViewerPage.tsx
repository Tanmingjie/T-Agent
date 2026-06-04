import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import Editor from "@monaco-editor/react";
import { apiGet } from "../api/client";
import FileTree from "../components/FileTree";

export default function CodeViewerPage() {
  const { id, runId, caseId } = useParams<{ id: string; runId: string; caseId: string }>();
  const navigate = useNavigate();
  const [files, setFiles] = useState<Record<string, string>>({});
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ files: Record<string, string> }>(`/suites/${id}/runs/${runId}/cases/${caseId}/code`)
      .then((data) => {
        setFiles(data.files);
        const first = Object.keys(data.files)[0];
        if (first) setSelectedFile(first);
      });
  }, [id, runId, caseId]);

  const currentContent = selectedFile ? files[selectedFile] : "";
  const lang = selectedFile?.endsWith(".feature") ? "gherkin" : "python";

  return (
    <div>
      <button onClick={() => navigate(`/suites/${id}/runs/${runId}/case/${caseId}`)} className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-surface-900 mb-3 transition-colors">
        ← 返回结果
      </button>

      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-surface-900">{caseId} — 代码</h1>
        <a
          href={`/api/suites/${id}/runs/${runId}/cases/${caseId}/code/download`}
          className="bg-brand-600 text-white px-4 py-1.5 rounded-md text-sm font-medium hover:bg-brand-700 transition-colors"
        >
          下载 .zip
        </a>
      </div>

      <div className="grid grid-cols-[200px_1fr] gap-4" style={{ height: "70vh" }}>
        <div className="bg-white border rounded p-4">
          <FileTree files={files} onSelect={setSelectedFile} selected={selectedFile} />
        </div>
        <div className="bg-white border rounded overflow-hidden">
          <Editor
            height="100%"
            language={lang}
            value={currentContent}
            theme="vs-dark"
            options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13 }}
          />
        </div>
      </div>
    </div>
  );
}
