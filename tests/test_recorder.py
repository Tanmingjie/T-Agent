"""T-09 单元测试:执行录制器。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from harness.recorder import Recorder
from input.models import ActionStep


def _step(no, tool="browser_click", heals=None):
    return ActionStep(
        step_no=no,
        tool_name=tool,
        tool_input={"ref": f"b{no}"},
        reasoning=f"思考{no}",
        intent=f"意图{no}",
        tool_result=f"结果{no}",
        url="http://x/p",
        heal_attempts=heals or [],
    )


def test_exec_id_generated_and_record_init():
    rec = Recorder("TC001", suite_id="S1")
    assert len(rec.exec_id) == 12
    assert rec.record.case_id == "TC001"
    assert rec.record.suite_id == "S1"
    assert rec.record.start_time > 0


def test_explicit_exec_id():
    rec = Recorder("TC001", exec_id="fixed123")
    assert rec.exec_id == "fixed123"


def test_add_and_extend_steps():
    rec = Recorder("TC001")
    rec.add_step(_step(1))
    rec.extend_steps([_step(2), _step(3)])
    assert len(rec.record.steps) == 3


def test_heal_count_accumulated():
    rec = Recorder("TC001")
    rec.add_step(_step(1, heals=[{"strategy": "P1"}, {"strategy": "P2"}]))
    rec.add_step(_step(2, heals=[{"strategy": "P1"}]))
    assert rec.record.heal_count == 3


def test_attach_step_assertions():
    rec = Recorder("TC001")
    rec.add_step(_step(1))
    rec.attach_step_assertions(1, [{"type": "element_visible", "status": "pass"}])
    assert rec.record.steps[0].assertion_results[0]["status"] == "pass"


def test_finalize_pass_with_auto_summary():
    rec = Recorder("TC001")
    rec.add_step(_step(1))
    rec.set_case_assertions(
        [
            {"type": "url_contains", "target": "URL", "status": "pass", "actual": "http://x/list"},
            {"type": "text_equals", "target": "状态", "status": "pass", "reason": ""},
        ]
    )
    record = rec.finalize(passed=True)
    assert record.passed
    assert record.end_time >= record.start_time
    assert "[PASS]" in record.final_result
    assert "✓" in record.final_result


def test_finalize_fail_summary_shows_failed_assertion():
    rec = Recorder("TC001")
    rec.set_case_assertions(
        [{"type": "text_equals", "target": "状态", "status": "fail", "reason": "文本不符"}]
    )
    record = rec.finalize(passed=False)
    assert "[FAIL]" in record.final_result
    assert "✗" in record.final_result
    assert "文本不符" in record.final_result


def test_finalize_custom_result_overrides_summary():
    rec = Recorder("TC001")
    record = rec.finalize(passed=True, final_result="自定义结论")
    assert record.final_result == "自定义结论"


def test_set_token_usage():
    rec = Recorder("TC001")
    rec.set_token_usage(1234)
    assert rec.record.token_usage == 1234


def test_screenshot_path_creates_dir():
    with tempfile.TemporaryDirectory() as tmp:
        rec = Recorder("TC001", exec_id="e1", screenshot_root=tmp)
        p = rec.screenshot_path(2)
        assert p.endswith("e1/step_002.png")
        assert Path(p).parent.is_dir()


def test_to_history_separates_output_and_result():
    rec = Recorder("TC001")
    rec.add_step(_step(1))
    hist = rec.to_history()
    assert hist[0]["model_output"]["reasoning"] == "思考1"
    assert hist[0]["model_output"]["tool_name"] == "browser_click"
    assert hist[0]["action_result"]["tool_result"] == "结果1"
    assert hist[0]["action_result"]["url"] == "http://x/p"
    # 思考与结果分离:model_output 不含 tool_result
    assert "tool_result" not in hist[0]["model_output"]


def test_to_dict_full_serialization():
    rec = Recorder("TC001")
    rec.add_step(_step(1))
    rec.set_case_assertions([{"type": "url_contains", "status": "pass"}])
    rec.finalize(passed=True)
    d = rec.to_dict()
    assert d["exec_id"] == rec.exec_id
    assert d["case_assertions"][0]["status"] == "pass"
    assert len(d["history"]) == 1
