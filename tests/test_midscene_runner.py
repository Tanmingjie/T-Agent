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


def test_midscene_runner_context_options_load_state_only_when_requested(tmp_path):
    state_path = tmp_path / "state.json"
    out = _node_eval(f"""
        const {{ browserContextOptions }} = require('./scripts/midscene_runner.js');
        const plain = browserContextOptions({{}}, {{}});
        const load = browserContextOptions(
          {{ storage_state_path: {json.dumps(str(state_path))}, capture_storage_state: false }},
          {{}}
        );
        const capture = browserContextOptions(
          {{ storage_state_path: {json.dumps(str(state_path))}, capture_storage_state: true }},
          {{}}
        );
        console.log(JSON.stringify({{ plain, load, capture }}));
        """)

    options = json.loads(out)
    assert "storageState" not in options["plain"]
    assert options["load"]["storageState"] == os.path.abspath(state_path)
    assert "storageState" not in options["capture"]


def test_midscene_runner_captures_storage_state_with_indexed_db(tmp_path):
    state_path = tmp_path / "auth" / "state.json"
    out = _node_eval(f"""
        const fs = require('node:fs');
        const {{ captureStorageState }} = require('./scripts/midscene_runner.js');
        const calls = [];
        const context = {{
          storageState: async (options) => {{
            calls.push(options);
            fs.writeFileSync(options.path, '{{"cookies":[],"origins":[]}}', 'utf8');
          }}
        }};
        captureStorageState(context, {json.dumps(str(state_path))})
          .then(() => console.log(JSON.stringify(calls[0])));
        """)

    options = json.loads(out)
    assert options == {"path": os.path.abspath(state_path), "indexedDB": True}
    assert state_path.is_file()


def test_midscene_runner_reports_storage_state_capture_failure(tmp_path):
    state_path = tmp_path / "auth" / "state.json"
    out = _node_eval(f"""
        const {{ captureStorageState }} = require('./scripts/midscene_runner.js');
        const context = {{ storageState: async () => {{ throw new Error('capture denied'); }} }};
        captureStorageState(context, {json.dumps(str(state_path))})
          .then(() => console.log('unexpected-success'))
          .catch((error) => console.log(error.message));
        """)

    assert out == "capture denied"
    assert not state_path.exists()


def test_midscene_runner_only_adds_url_guidance_for_url_expectations():
    out = _node_eval("""
        const { buildAssertInstruction } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify({
          visibleText: buildAssertInstruction('页面出现文案 Products', 'https://example.com/a'),
          url: buildAssertInstruction('URL 包含 inventory.html', 'https://example.com/inventory.html')
        }));
        """)

    instructions = json.loads(out)
    assert "当前页面 URL" not in instructions["visibleText"]
    assert "URL 条件" not in instructions["visibleText"]
    assert "当前页面 URL: https://example.com/inventory.html" in instructions["url"]
    assert "预期包含 URL 条件" in instructions["url"]


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


def test_midscene_runner_splits_normal_steps_into_individual_ai_acts():
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
    assert [s["kind"] for s in segments] == [
        "aiAct",
        "aiAct",
        "sleep",
        "aiAct",
        "sleep",
        "aiAct",
    ]
    assert segments[0]["steps"] == ["输入用户名"]
    assert segments[1]["steps"] == ["输入密码"]
    assert segments[2]["duration_ms"] == 180000
    assert segments[3]["steps"] == ["点击查询"]
    assert segments[4]["duration_ms"] == 30000
    assert segments[5]["steps"] == ["查看结果"]


def test_midscene_runner_supports_configurable_ai_act_chunk_size():
    out = _node_eval("""
        const { splitPhaseSteps } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify(splitPhaseSteps(['步骤1', '步骤2', '步骤3'], 2)));
        """)

    segments = json.loads(out)
    assert [s["steps"] for s in segments] == [["步骤1", "步骤2"], ["步骤3"]]
    assert [s["step_index"] for s in segments] == [0, 2]


def test_midscene_runner_uses_one_step_per_ai_act_by_default():
    out = _node_eval("""
        const { resolveMaxStepsPerAct } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify([
          resolveMaxStepsPerAct({}),
          resolveMaxStepsPerAct({ MIDSCENE_MAX_STEPS_PER_ACT: '3' }),
          resolveMaxStepsPerAct({ MIDSCENE_MAX_STEPS_PER_ACT: '0' }),
          resolveMaxStepsPerAct({ MIDSCENE_MAX_STEPS_PER_ACT: 'invalid' })
        ]));
        """)

    assert json.loads(out) == [1, 3, 1, 1]


def test_midscene_runner_uses_fifteen_minute_ai_act_timeout_default():
    out = _node_eval("""
        const { resolveAiActTimeoutMs } = require('./scripts/midscene_runner.js');
        console.log(JSON.stringify([
          resolveAiActTimeoutMs({}),
          resolveAiActTimeoutMs({ MIDSCENE_AI_ACT_TIMEOUT_SECONDS: '60' }),
          resolveAiActTimeoutMs({ MIDSCENE_AI_ACT_TIMEOUT_SECONDS: '0' }),
          resolveAiActTimeoutMs({ MIDSCENE_AI_ACT_TIMEOUT_SECONDS: 'invalid' })
        ]));
        """)

    assert json.loads(out) == [900000, 60000, 0, 900000]


def test_midscene_runner_aborts_ai_act_after_timeout():
    out = _node_eval("""
        const { runAiActWithTimeout } = require('./scripts/midscene_runner.js');
        const agent = {
          aiAct: (_instruction, options) => new Promise((_resolve, reject) => {
            options.abortSignal.addEventListener(
              'abort',
              () => reject(options.abortSignal.reason),
              { once: true }
            );
          })
        };
        runAiActWithTimeout(agent, '一直不完成的步骤', 10)
          .then(() => console.log('unexpected-success'))
          .catch((error) => console.log(JSON.stringify({
            code: error.code,
            message: error.message
          })));
        """)

    error = json.loads(out)
    assert error["code"] == "MIDSCENE_AI_ACT_TIMEOUT"
    assert "0.01s" in error["message"]


def test_midscene_runner_does_not_feed_phase_expected_into_ai_act():
    out = _node_eval("""
        const { buildSegmentInstruction } = require('./scripts/midscene_runner.js');
        console.log(buildSegmentInstruction(0, 0, ['输入用户名'], '最终应进入首页'));
        """)

    assert "输入用户名" in out
    assert "最终应进入首页" not in out
    assert "不要提前执行后续步骤" in out


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
