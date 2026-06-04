"""CodeGenerator 抽象基类(规格 §5.6,T-20)。

抽象接口:``generate(spec, record) -> GeneratedCode``,Suite 配置时选择具体实现
(默认 BDDGenerator)。这里同时定义多文件产物 ``GeneratedCode`` 与落盘逻辑。

注:规格写的是 ``generate(record)``,但 BDD 的 Given/When/Then 来自**业务粒度的
TestSpec**(而非 tool_call 粒度的录制),故签名取 ``(spec, record)``——record 提供
执行期的真实信息(URL/选择器等)用于增强生成的代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from codegen.locators import Locator
from input.models import ExecutionRecord, TestSpec


@dataclass
class GeneratedCode:
    """一次代码生成的多文件产物。"""

    name: str  # 基名(通常 case_id),决定文件名
    feature: str  # Gherkin .feature
    step_defs: str  # pytest-bdd step 定义(.py)
    conftest: str  # conftest.py

    def write(self, out_dir: str | Path) -> dict[str, str]:
        """写出 ``<name>.feature`` / ``test_<name>.py`` / ``conftest.py``。返回路径映射。"""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "feature": out / f"{self.name}.feature",
            "step_defs": out / f"test_{self.name}.py",
            "conftest": out / "conftest.py",
        }
        paths["feature"].write_text(self.feature, encoding="utf-8")
        paths["step_defs"].write_text(self.step_defs, encoding="utf-8")
        paths["conftest"].write_text(self.conftest, encoding="utf-8")
        return {k: str(v) for k, v in paths.items()}


class CodeGenerator(ABC):
    """代码生成器抽象接口。

    ``locators``:解析层(框架无关)预解析好的 {语义 target: Locator},由各实现渲染成
    自身框架语法。为空/未命中的 target 由实现回退启发式定位。
    """

    @abstractmethod
    def generate(
        self,
        spec: TestSpec,
        record: ExecutionRecord,
        locators: dict[str, Locator] | None = None,
    ) -> GeneratedCode: ...
