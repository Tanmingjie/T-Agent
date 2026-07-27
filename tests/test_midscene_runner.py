from __future__ import annotations

import json
import os
import subprocess


def _node_eval(script: str) -> str:
    proc = subprocess.run(
        ["node", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_midscene_runner_reports_missing_model_config_clearly():
    payload = {
        "run_id": "runner-smoke",
        "case_id": "tc1",
        "base_url": "https://example.com",
        "artifact_dir": "storage/midscene-smoke/test-missing-config",
        "spec": {
            "intent": "smoke",
            "phases": [{"steps": ["noop"], "expected": "noop"}],
        },
        "model_config": {},
    }
    env = {
        **os.environ,
        "MIDSCENE_MODEL_NAME": "",
        "MIDSCENE_MODEL_BASE_URL": "",
        "MIDSCENE_MODEL_API_KEY": "",
        "MIDSCENE_MODEL_FAMILY": "",
        "MIDSCENE_REUSE_LLM_CONFIG": "0",
    }

    proc = subprocess.run(
        ["node", "scripts/midscene_runner.js"],
        input="\ufeff" + json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["passed"] is False
    assert data["stop_reason"] == "runner_exception"
    assert "Missing Midscene model config" in data["error"]
    assert "MIDSCENE_MODEL_FAMILY" in data["error"]


def test_midscene_runner_uses_conservative_wait_after_action_default():
    out = _node_eval("""
        const { resolveWaitAfterActionMs } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify([
          resolveWaitAfterActionMs({}),
          resolveWaitAfterActionMs({ MIDSCENE_WAIT_AFTER_ACTION_MS: '3500' }),
          resolveWaitAfterActionMs({ MIDSCENE_WAIT_AFTER_ACTION_MS: 'invalid' })
        ]));
        """)

    assert json.loads(out) == [2000, 3500, 2000]


def test_midscene_runner_splits_wait_steps_without_splitting_normal_steps():
    out = _node_eval("""
        const { splitPhaseSteps } = require('./scripts/midscene_runner.js');
        const segments = splitPhaseSteps([
          '输入用户名',
          '输入密码',
          '等待3分钟以生成数据',
          '点击查询',
          '观察30秒',
          '查看结果'
        ]);
        console.log(JSON.stringify(segments));
        """)

    segments = json.loads(out)
    assert [s["kind"] for s in segments] == ["aiAct", "sleep", "aiAct", "sleep", "aiAct"]
    assert segments[0]["steps"] == ["输入用户名", "输入密码"]
    assert segments[1]["duration_ms"] == 180000
    assert segments[2]["steps"] == ["点击查询"]
    assert segments[3]["duration_ms"] == 30000
    assert segments[4]["steps"] == ["查看结果"]


def test_midscene_runner_turns_condition_wait_without_duration_into_ai_wait_for():
    out = _node_eval("""
        const { splitPhaseSteps } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify(splitPhaseSteps(['点击刷新', '等待页面刷新完成', '查看结果'])));
        """)

    segments = json.loads(out)
    assert [s["kind"] for s in segments] == ["aiAct", "aiWaitFor", "aiAct"]
    assert segments[0]["steps"] == ["点击刷新"]
    assert segments[1]["condition"] == "等待页面刷新完成"
    assert segments[2]["steps"] == ["查看结果"]


def test_midscene_runner_parses_chinese_wait_duration():
    out = _node_eval("""
        const { parseWaitStep } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify([
          parseWaitStep('观察三十秒'),
          parseWaitStep('等待两分钟生成数据'),
          parseWaitStep('暂停100ms')
        ]));
        """)

    parsed = json.loads(out)
    assert parsed == [
        {"duration_ms": 30000},
        {"duration_ms": 120000},
        {"duration_ms": 100},
    ]


def test_midscene_runner_splits_conditional_wait_to_ai_wait_for():
    out = _node_eval("""
        const { splitPhaseSteps } = require('./scripts/midscene_runner.js');
        const segments = splitPhaseSteps([
          '点击开始按钮',
          '观察液位值,等到液位低于30后点击停止按钮',
          '查看停止结果'
        ]);
        console.log(JSON.stringify(segments));
        """)

    segments = json.loads(out)
    assert [s["kind"] for s in segments] == ["aiAct", "aiWaitFor", "aiAct", "aiAct"]
    assert segments[0]["steps"] == ["点击开始按钮"]
    assert segments[1]["condition"] == "观察液位值,等到液位低于30"
    assert segments[2]["steps"] == ["点击停止按钮"]
    assert segments[3]["steps"] == ["查看停止结果"]


def test_midscene_runner_keeps_fixed_duration_wait_as_sleep_before_conditional_wait():
    out = _node_eval("""
        const { splitPhaseSteps } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify(splitPhaseSteps([
          '等待30秒',
          '直到状态变为已完成'
        ])));
        """)

    segments = json.loads(out)
    assert [s["kind"] for s in segments] == ["sleep", "aiWaitFor"]
    assert segments[0]["duration_ms"] == 30000
    assert segments[1]["condition"] == "直到状态变为已完成"


def test_midscene_runner_parses_conditional_wait_step_without_followup():
    out = _node_eval("""
        const { parseConditionalWaitStep } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify(parseConditionalWaitStep('等待列表状态变为已完成')));
        """)

    parsed = json.loads(out)
    assert parsed == {"condition": "等待列表状态变为已完成", "followup": ""}
