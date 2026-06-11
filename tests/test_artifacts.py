"""T-P10 单元测试:产物存储抽象(本地实现)。"""

from __future__ import annotations

from storage.artifacts import ArtifactStore, LocalArtifactStore


def test_screenshot_dir_bucketed_by_run_case(tmp_path):
    store = LocalArtifactStore(tmp_path)
    d = store.screenshot_dir("run1", "caseA")
    assert d == tmp_path / "screenshots" / "run1" / "caseA"


def test_read_screenshot_roundtrip(tmp_path):
    store = LocalArtifactStore(tmp_path)
    d = store.screenshot_dir("run1", "caseA")
    d.mkdir(parents=True)
    (d / "step_001.png").write_bytes(b"PNGDATA")
    assert store.read_screenshot("run1", "caseA", "step_001.png") == b"PNGDATA"
    assert store.read_screenshot("run1", "caseA", "missing.png") is None


def test_read_screenshot_blocks_traversal(tmp_path):
    store = LocalArtifactStore(tmp_path)
    (tmp_path / "secret.txt").write_bytes(b"top secret")
    # 试图穿越目录 → 只取基名 → 命不中
    assert store.read_screenshot("run1", "caseA", "../../secret.txt") is None


def test_read_generated_files(tmp_path):
    store = LocalArtifactStore(tmp_path)
    gen = store.generated_dir()
    gen.mkdir(parents=True)
    (gen / "TC1.feature").write_text("Feature: x", encoding="utf-8")
    (gen / "test_TC1.py").write_text("def test(): pass", encoding="utf-8")
    files = store.read_generated("TC1")
    assert files["TC1.feature"] == "Feature: x"
    assert files["test_TC1.py"] == "def test(): pass"
    assert store.read_generated("nope") == {}


def test_env_root_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "custom"))
    store = LocalArtifactStore()
    assert store.screenshots_root == tmp_path / "custom" / "screenshots"


def test_local_is_artifact_store():
    assert issubclass(LocalArtifactStore, ArtifactStore)
