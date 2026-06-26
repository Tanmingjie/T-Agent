// 用例规范(项目级)。注入翻译 prompt(intelligence/pre_analysis):助补全流程、对齐术语、
// 写对阶段预期。受后端两条护栏约束(仍不接地、不脑补 expected)。全项目用例共用。
// 后端字段标识符仍是 project.translation_knowledge(显示名为「用例规范」)。
import { useEffect, useState } from "react";
import { BookText, Check } from "lucide-react";
import { apiGet, apiPut } from "../api/client";
import { getProjectId } from "../lib/session";

export default function CaseSpecPage() {
  const pid = getProjectId();
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!pid) return;
    apiGet<{ translation_knowledge?: string }>(`/projects/${pid}`)
      .then((p) => setText(p.translation_knowledge || ""))
      .finally(() => setLoaded(true));
  }, [pid]);

  if (!pid) {
    return (
      <div className="text-sm text-gray-500">
        未指定项目。请通过内网系统进入,或在 URL 加 ?project=&lt;id&gt;。
      </div>
    );
  }

  async function save() {
    await apiPut(`/projects/${pid}`, { translation_knowledge: text });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div className="max-w-3xl">
      <div className="mb-5 flex items-center gap-2">
        <BookText size={20} className="text-brand-600" />
        <h1 className="text-xl font-semibold text-surface-900">用例规范</h1>
      </div>
      <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-3">
        <p className="text-xs text-gray-500 leading-relaxed">
          用自然语言写本系统的业务规范:流程怎么走、业务术语对应、操作成功后页面长什么样。
          翻译用例时会用它补全隐含步骤、对齐术语、把阶段预期写成可核验的真实状态——从而提升执行与裁决质量。
          <br />
          注意:它<b>只在自然语言层</b>帮助理解,系统<b>不会</b>据此写选择器、也<b>不应</b>把"理想态"
          当成页面一定出现的东西(只写真实可观察的状态),避免反而误判失败。全项目用例共用。
        </p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!loaded}
          rows={18}
          placeholder={
            "例:\n- 提交报销单前必须先选择审批人,否则提交按钮不可点。\n" +
            "- 业务术语:本系统把「商品」叫「标的物」。\n" +
            "- 下单成功后跳转到「我的订单」列表,状态列显示「待审批」。"
          }
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm font-mono leading-relaxed"
        />
        <div className="flex items-center gap-3">
          <button
            onClick={save}
            disabled={!loaded}
            className="bg-brand-600 text-white px-4 py-2 rounded-md text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            保存
          </button>
          {saved && (
            <span className="text-sm text-green-600 inline-flex items-center gap-1">
              <Check size={15} /> 已保存
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
