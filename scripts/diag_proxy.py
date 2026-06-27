"""诊断:Python 这次请求到底走没走代理,以及直连能否成功。
用法:在出问题的同一个 cmd 里:py -3.11 scripts\diag_proxy.py
"""
import json
import os
import sys

# 你的内网 LLM base(按需改;留 None 则读 env LLM_API_BASE)
BASE = os.getenv("LLM_API_BASE") or "http://你的内网LLM主机/v1"
URL = BASE.rstrip("/") + "/chat/completions"

print("== 进程看到的代理相关环境变量 ==")
for k, v in os.environ.items():
    if "proxy" in k.lower():
        print(f"  {k}={v}")
print()

import httpx

# httpx 对这个 URL 解析出的代理(关键证据)
print("== httpx 对该 URL 解析到的代理 ==")
try:
    client = httpx.Client()
    tport = client._transport_for_url(httpx.URL(URL))
    print("  transport:", type(tport).__name__, getattr(tport, "_pool", ""))
except Exception as e:
    print("  (无法内省,换下面 mounts 看)", e)
print("  环境推导 proxies:", httpx._utils.get_environment_proxies() if hasattr(httpx._utils,"get_environment_proxies") else "n/a")
print()

# A) 默认(读 env,可能走代理)
print("== A) 默认 httpx(trust_env=True,会读代理)==")
try:
    r = httpx.post(URL, json={"model":"x","messages":[{"role":"user","content":"ping"}]}, timeout=15)
    print("  status:", r.status_code, "| body[:120]:", r.text[:120].replace("\n"," "))
except Exception as e:
    print("  ERR:", type(e).__name__, str(e)[:200])
print()

# B) 强制不读环境代理(直连)——若这个成功,就 100% 是代理问题
print("== B) trust_env=False 直连(忽略所有代理 env)==")
try:
    with httpx.Client(trust_env=False, timeout=15) as c:
        r = c.post(URL, json={"model":"x","messages":[{"role":"user","content":"ping"}]})
    print("  status:", r.status_code, "| body[:120]:", r.text[:120].replace("\n"," "))
except Exception as e:
    print("  ERR:", type(e).__name__, str(e)[:200])
