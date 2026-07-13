"""产物存储抽象(平台化 T-P10)。

截图 / 生成代码等执行产物的路径与读写收口到一个接口,业务码不再散落 `storage/screenshots`、
`storage/generated` 字面量。本地用文件系统(`LocalArtifactStore`);M3 换对象存储 / PVC
只换实现(`ArtifactStore` 子类),按 run/case 分桶的 key 约定不变。

约定:
- 截图 key:``screenshots/<run_id>/<case_id>/<filename>``(filename 形如 step_003.png)。
- 生成代码:``generated/<name>.feature`` 与 ``generated/test_<name>.py``。

读接口返回 bytes/str(对象存储可实现);写侧本地用真实路径(`screenshot_dir`/`generated_dir`),
M3 对象存储改为流式上传时再统一(recorder/codegen 当前直接写文件系统)。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path


class ArtifactStore(ABC):
    """产物读写接口(本地 / 对象存储可换实现)。"""

    @abstractmethod
    def screenshot_dir(self, run_id: str, case_id: str) -> Path:
        """截图写入目录(本地实现返回真实路径;recorder 据此落盘)。"""

    @abstractmethod
    def read_screenshot(self, run_id: str, case_id: str, filename: str) -> bytes | None:
        """读一张截图;不存在返回 None。"""

    @abstractmethod
    def generated_dir(self) -> Path:
        """生成代码写入目录。"""

    @abstractmethod
    def read_generated(self, name: str) -> dict[str, str]:
        """读某用例的生成代码文件:{文件名: 文本}(不存在则空 dict)。"""

    @abstractmethod
    def midscene_dir(self, run_id: str, case_id: str) -> Path:
        """Midscene 原生产物目录。"""


class LocalArtifactStore(ArtifactStore):
    """文件系统实现(单机/开发)。根目录默认 ``storage``,env ``ARTIFACT_ROOT`` 可覆盖。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.getenv("ARTIFACT_ROOT", "storage"))

    @property
    def screenshots_root(self) -> Path:
        return self.root / "screenshots"

    @property
    def generated_root(self) -> Path:
        return self.root / "generated"

    def screenshot_dir(self, run_id: str, case_id: str) -> Path:
        return self.screenshots_root / run_id / case_id

    def read_screenshot(self, run_id: str, case_id: str, filename: str) -> bytes | None:
        # 防目录穿越:filename 只取基名
        path = self.screenshot_dir(run_id, case_id) / Path(filename).name
        if not path.is_file():
            return None
        return path.read_bytes()

    def generated_dir(self) -> Path:
        return self.generated_root

    def read_generated(self, name: str) -> dict[str, str]:
        out: dict[str, str] = {}
        feat = self.generated_root / f"{name}.feature"
        steps = self.generated_root / f"test_{name}.py"
        if feat.is_file():
            out[f"{name}.feature"] = feat.read_text(encoding="utf-8")
        if steps.is_file():
            out[f"test_{name}.py"] = steps.read_text(encoding="utf-8")
        return out

    def midscene_dir(self, run_id: str, case_id: str) -> Path:
        return self.root / "midscene" / run_id / case_id


_default: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    """进程级默认产物存储(M3 可在此按 env 切对象存储实现)。"""
    global _default
    if _default is None:
        _default = LocalArtifactStore()
    return _default
