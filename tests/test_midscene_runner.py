from __future__ import annotations

import json
import os
import subprocess


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
