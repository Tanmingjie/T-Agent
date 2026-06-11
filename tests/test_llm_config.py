"""T-P06 单元测试:项目级 LLM 配置(加密落库 + per-run 构造)。"""

from __future__ import annotations

import pytest

from harness.llm import LiteLLMClient, build_llm_client
from input.models import ProjectLLMConfig
from storage import crypto
from storage.db import ProjectLLMConfigRow, Store


@pytest.fixture
async def store(tmp_path):
    s = Store(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await s.init()
    yield s
    await s.close()


# ── 加密模块 ─────────────────────────────────────────────────


def test_crypto_roundtrip():
    token = crypto.encrypt("sk-secret-123")
    assert token != "sk-secret-123"  # 密文不等于明文
    assert crypto.decrypt(token) == "sk-secret-123"


def test_crypto_empty_passthrough():
    assert crypto.encrypt("") == ""
    assert crypto.encrypt(None) == ""
    assert crypto.decrypt("") == ""


def test_crypto_decrypt_garbage_returns_empty():
    assert crypto.decrypt("not-a-valid-token") == ""  # 脏数据不炸,返 ""


def test_crypto_mask_shows_tail_only():
    m = crypto.mask("sk-abcd1234")
    assert m.endswith("1234")
    assert "sk-abcd" not in m
    assert crypto.mask("") == ""


# ── Store:加密落库 ──────────────────────────────────────────


async def test_llm_config_roundtrip_decrypts(store):
    cfg = ProjectLLMConfig(
        project_id="p1", model="openai/gpt-x", api_base="http://gw/v1", api_key="sk-xyz"
    )
    await store.save_llm_config(cfg)
    got = await store.get_llm_config("p1")
    assert got.model == "openai/gpt-x"
    assert got.api_base == "http://gw/v1"
    assert got.api_key == "sk-xyz"  # 读回解密为明文


async def test_llm_config_api_key_encrypted_at_rest(store):
    await store.save_llm_config(ProjectLLMConfig(project_id="p1", model="m", api_key="sk-plain"))
    # 直接读表行:落库的是密文,不是明文
    async with store._sf() as s:
        row = await s.get(ProjectLLMConfigRow, "p1")
    assert row.api_key_encrypted != "sk-plain"
    assert row.api_key_encrypted != ""
    assert crypto.decrypt(row.api_key_encrypted) == "sk-plain"


async def test_llm_config_upsert_and_delete(store):
    await store.save_llm_config(ProjectLLMConfig(project_id="p1", model="m1", api_key="k1"))
    await store.save_llm_config(ProjectLLMConfig(project_id="p1", model="m2", api_key="k2"))
    got = await store.get_llm_config("p1")
    assert got.model == "m2" and got.api_key == "k2"  # upsert,非两行
    assert await store.delete_llm_config("p1") is True
    assert await store.get_llm_config("p1") is None
    assert await store.delete_llm_config("nope") is False


# ── per-run 构造 ─────────────────────────────────────────────


def test_build_llm_client_from_config_overrides():
    cfg = ProjectLLMConfig(
        project_id="p1", model="openai/foo", api_base="http://gw/v1", api_key="sk-1"
    )
    llm = build_llm_client(cfg)
    assert isinstance(llm, LiteLLMClient)
    assert llm.model == "openai/foo"
    assert llm.api_base == "http://gw/v1"
    assert llm.api_key == "sk-1"


def test_build_llm_client_none_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "ollama/envmodel")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    llm = build_llm_client(None)
    assert llm.model == "ollama/envmodel"  # 回退 env


def test_build_llm_client_empty_fields_keep_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "ollama/envmodel")
    # 配置只给 api_base、不给 model → model 仍走 env(混合)
    cfg = ProjectLLMConfig(project_id="p1", model="", api_base="http://gw/v1")
    llm = build_llm_client(cfg)
    assert llm.model == "ollama/envmodel"
    assert llm.api_base == "http://gw/v1"
