interface Step {
  step_no: number;
  tool_name: string;
  reasoning: string;
  screenshot: string | null;
  assertion_results: { type: string; status: string; target: string; reason?: string }[];
}

interface Props {
  steps: Step[];
  onSelect: (stepNo: number) => void;
  selected: number | null;
}

export default function StepListPanel({ steps, onSelect, selected }: Props) {
  return (
    <div>
      <h3 className="font-semibold mb-2">步骤</h3>
      {steps.map((s) => (
        <div key={s.step_no}>
          <button
            onClick={() => onSelect(s.step_no)}
            className={`w-full text-left px-3 py-2 text-sm rounded mb-1 ${
              selected === s.step_no ? "bg-brand-50" : "hover:bg-gray-50"
            }`}
          >
            <span className="text-green-500 mr-1">✅</span>
            Step {s.step_no}: {s.tool_name}
          </button>
          {s.assertion_results.length > 0 && (
            <div className="ml-6 mb-2">
              {s.assertion_results.map((a, i) => (
                <div key={i} className="text-xs text-gray-500">
                  {a.status === "pass" ? "✓" : "✗"} [{a.type}] {a.target}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
