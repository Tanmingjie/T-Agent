"""Dev 服务启动器:uvicorn --reload,但**只监视源码目录**,不监视运行时产物。

为什么需要(血泪坑):执行**通过**的用例会由 codegen 往 `storage/generated/` 写
pytest 代码(`.py`)。默认 `uvicorn --reload` 监视整个项目目录,会因这些写入
**重启整个后端**——后果是:正在跑的 run 被拦腰打断、SSE 断开、所有在途 HTTP 请求
在重启窗口里 pending(用户现象:"一条用例执行完,所有请求卡几秒")。

解法:用 `reload_dirs` 把 reload 限定在**源码目录**,storage/ 的写入永不触发重启。
代价:`storage/db.py` 的改动不会热重载(很少改;需要时手动重启)。

用法:`python scripts/serve.py`(替代 `uvicorn api.server:app --reload`)。
"""

from __future__ import annotations

import os
import sys

import uvicorn

# 项目根(scripts/ 的上一级)。本机为 embeddable Python:cwd 不在 sys.path,
# 且 reload 会另起子进程(不继承本进程的 sys.path 修改)→ 必须经 PYTHONPATH 传给子进程,
# 否则子进程 `import api` 报 ModuleNotFoundError。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_existing = os.environ.get("PYTHONPATH", "")
if _ROOT not in _existing.split(os.pathsep):
    os.environ["PYTHONPATH"] = _ROOT + (os.pathsep + _existing if _existing else "")

# 只监视源码目录(不含 storage/ 这类运行时产物落地处)
SOURCE_DIRS = [
    "api",
    "harness",
    "codegen",
    "intelligence",
    "input",
    "cli",
]

if __name__ == "__main__":
    uvicorn.run(
        "api.server:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
        reload_dirs=[os.path.join(_ROOT, d) for d in SOURCE_DIRS],
    )
