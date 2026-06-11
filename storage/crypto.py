"""字段级对称加密(平台化 T-P06)。

用于把 LLM api_key、Session Cookie、工具凭据等**敏感字段加密落库**(规格/草案 §4 安全)。
对称加密用 Fernet(AES-128-CBC + HMAC);平台密钥从 env ``PLATFORM_SECRET_KEY`` 读
(一个 urlsafe-base64 的 32 字节 Fernet key,生成:``Fernet.generate_key()``)。

**开发兜底**:未设 ``PLATFORM_SECRET_KEY`` 时用一个固定的开发密钥(仅本机/测试便利),
并打一次告警——**生产必须设 env**,否则等同明文(密钥写在代码里)。

约定:空串 → 空串(不加密空值);``decrypt`` 对解不开的串返回 ""(密钥换过/脏数据时
不炸链路,值不可用即重配),并告警。
"""

from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# 固定开发兜底密钥(**仅开发/测试**;生产用 PLATFORM_SECRET_KEY 覆盖)。
# 合法 Fernet key(32B urlsafe-base64);写死在代码里=不安全,仅图本机/测试便利。
_DEV_KEY = "Z8UAAl1H7qhXRjnzM46ll4ltTDgDJBPI5DdTFtnuDAM="
_warned = False


def _cipher() -> Fernet:
    global _warned
    key = os.getenv("PLATFORM_SECRET_KEY")
    if not key:
        if not _warned:
            logger.warning(
                "未设 PLATFORM_SECRET_KEY,字段加密使用开发兜底密钥(不安全)。"
                '生产请设置:PLATFORM_SECRET_KEY=$(python -c "from cryptography.fernet '
                'import Fernet;print(Fernet.generate_key().decode())")'
            )
            _warned = True
        key = _DEV_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str | None) -> str:
    """明文 → 密文串。空/None → ""(不加密空值)。"""
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(token: str | None) -> str:
    """密文串 → 明文。空 → "";解不开 → ""(脏数据/换密钥,不炸链路)+ 告警。"""
    if not token:
        return ""
    try:
        return _cipher().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as e:
        logger.warning("字段解密失败(密钥不匹配或脏数据):%s", type(e).__name__)
        return ""


def mask(secret: str | None, *, show: int = 4) -> str:
    """脱敏显示:只露尾 ``show`` 位,前面用 • 占位(界面回显用,不返明文)。"""
    if not secret:
        return ""
    tail = secret[-show:] if len(secret) > show else secret
    return "•" * 8 + tail
