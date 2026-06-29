"""生成 ThingsBoard demo 业务用例 Excel(工业 web 系统失败类挖掘素材)。

为什么选 ThingsBoard(https://thingsboard.cloud,需自备租户账号):
- IoT 监控平台,最接近用户内网"工艺模拟器"那类**工业 web 系统**:Angular Material SPA、
  token 型登录、左侧导航、设备数据表(mat-table)、**仪表盘 widget(图表多为 canvas/SVG)**。
- 比 saucedemo 脏得多:自定义 web component(mat-form-field/mat-nav-list)、异步加载、live
  重渲染、onboarding 浮层,能压测翻译/定位/快照可观测/裁决多条链路(本仓多条 dirty-SPA 修复由此挖出)。

**凭据**:不在源码硬编码。注册一个 thingsboard.cloud 账号后,经环境变量提供:
    TB_EMAIL=<你的邮箱> TB_PASSWORD=<你的密码> python examples/make_thingsboard_xlsx.py
未设时填占位符(生成的 xlsx 仅作模板,跑前需替换)。⚠️公网服务,用一次性密码。
生成的 thingsboard_cases.xlsx 含明文凭据,已 .gitignore 不入库。

用法:
    TB_EMAIL=... TB_PASSWORD=... python examples/make_thingsboard_xlsx.py
    # 脏 SPA 建议:STEP_FAIL_BUDGET=10 SNAPSHOT_MAX_LINES=150(现为默认)
    python cli/run_case.py --excel examples/thingsboard_cases.xlsx \\
        --case-id TB04 --base-url https://thingsboard.cloud --isolated --headless --max-steps 100

用例:TB01 登录 / TB02 设备表 / TB03 仪表盘 / TB04 综合复杂流程(含等待3分钟) / TB05 行钻取详情。
预期只写**页面真实可观察**的状态(a11y 可见),刻意不拿 canvas 图表内部值/随数据变动的具体值当 expected。
"""

from __future__ import annotations

import os
from pathlib import Path

from openpyxl import Workbook

# 凭据走环境变量,不硬编码(thingsboard.cloud 是用户真实账号)。未设填占位符。
EMAIL = os.getenv("TB_EMAIL", "<your-thingsboard-email>")
PASSWORD = os.getenv("TB_PASSWORD", "<your-password>")

CASES = [
    {
        "id": "TB01",
        "name": "租户登录进入首页",
        "preconditions": "1. 浏览器已打开\n2. 使用租户管理员账号 __TB_EMAIL__ / __TB_PASSWORD__",
        "steps": (
            "1. 打开登录页 https://thingsboard.cloud\n"
            "2. 在邮箱输入框填写 __TB_EMAIL__\n"
            "3. 在密码输入框填写 __TB_PASSWORD__\n"
            "4. 点击「Login」登录按钮"
        ),
        "expected": (
            "1. 登录成功后进入主页\n" "2. 左侧出现导航菜单(含 Home / Dashboards / Devices 等入口)"
        ),
    },
    {
        "id": "TB02",
        "name": "查看设备列表",
        "preconditions": "1. 已用租户账号 __TB_EMAIL__ / __TB_PASSWORD__ 登录",
        "steps": (
            "1. 打开登录页 https://thingsboard.cloud\n"
            "2. 在邮箱输入框填写 __TB_EMAIL__\n"
            "3. 在密码输入框填写 __TB_PASSWORD__\n"
            "4. 点击「Login」登录按钮\n"
            "5. 点击左侧导航的「Devices」进入设备管理\n"
            "6. 等待设备表格加载完成"
        ),
        "expected": ("1. 进入设备(Devices)页面\n" "2. 页面出现设备数据表格,且至少有一行设备记录"),
    },
    {
        "id": "TB03",
        "name": "打开仪表盘查看 widget",
        "preconditions": "1. 已用租户账号 __TB_EMAIL__ / __TB_PASSWORD__ 登录",
        "steps": (
            "1. 打开登录页 https://thingsboard.cloud\n"
            "2. 在邮箱输入框填写 __TB_EMAIL__\n"
            "3. 在密码输入框填写 __TB_PASSWORD__\n"
            "4. 点击「Login」登录按钮\n"
            "5. 点击左侧导航的「Dashboards」进入仪表盘列表\n"
            "6. 在仪表盘列表中点击第一个仪表盘打开它\n"
            "7. 等待仪表盘内的 widget 加载完成"
        ),
        "expected": (
            "1. 进入仪表盘列表页,出现仪表盘表格\n"
            "2. 打开某个仪表盘后,页面出现至少一个 widget 卡片(图表/数据卡)"
        ),
    },
    {
        "id": "TB04",
        "name": "工业监控综合流程(登录→设备→详情→仪表盘→长时观察)",
        "preconditions": (
            "1. 已用租户账号 __TB_EMAIL__ / __TB_PASSWORD__ 登录\n"
            "2. 租户下已有 Smart office 方案数据(含 HVAC 等设备与 Smart office 仪表盘)"
        ),
        "steps": (
            "1. 打开登录页 https://thingsboard.cloud\n"
            "2. 在邮箱输入框填写 __TB_EMAIL__\n"
            "3. 在密码输入框填写 __TB_PASSWORD__\n"
            "4. 点击「Login」登录按钮\n"
            "5. 若登录后出现欢迎引导浮层,先点「Got it」或关闭按钮把它关掉(没有则跳过)\n"
            "6. 点击左侧导航的「Devices」进入设备管理\n"
            "7. 等待设备表格加载完成,确认其中有「HVAC」设备\n"
            "8. 点击名称为「HVAC」的那一行,打开该设备详情面板\n"
            "9. 在 HVAC 详情面板中切换到「Latest telemetry」(最新遥测)标签页\n"
            "10. 点击左侧导航的「Dashboards」进入仪表盘列表\n"
            "11. 等待仪表盘列表加载完成,确认列表中有「Smart office」仪表盘\n"
            "12. 在仪表盘列表页面原地等待 3 分钟,期间持续观察页面\n"
            "13. 等待结束后重新查看页面,确认仍停留在仪表盘列表页"
        ),
        "expected": (
            "1. 登录成功进入系统,URL 不再包含 /login\n"
            "2. 进入设备页面,设备表格加载完成,出现「HVAC」记录行\n"
            "3. 打开 HVAC 详情后切到「Latest telemetry」,出现遥测数据键值行(如 temperature 等指标)\n"
            "4. 进入仪表盘列表页(URL 含 dashboards),列表中出现「Smart office」记录行\n"
            "5. 原地等待 3 分钟后,页面仍停留在仪表盘列表页(URL 含 dashboards、列表仍在),"
            "未崩溃、未退回登录页"
        ),
    },
    {
        "id": "TB05",
        "name": "打开设备详情(硬交互探针:点 mat-table 行)",
        "preconditions": "1. 已用租户账号 __TB_EMAIL__ / __TB_PASSWORD__ 登录\n2. 租户下有 HVAC 设备",
        "steps": (
            "1. 打开登录页 https://thingsboard.cloud\n"
            "2. 在邮箱输入框填写 __TB_EMAIL__\n"
            "3. 在密码输入框填写 __TB_PASSWORD__\n"
            "4. 点击「Login」登录按钮\n"
            "5. 若登录后出现欢迎引导浮层,先关掉它(没有则跳过)\n"
            "6. 点击左侧导航的「Devices」进入设备管理\n"
            "7. 在设备表格中点击名称为「HVAC」的那一行,打开该设备的详情面板\n"
            "8. 在 HVAC 详情面板中切换到「Latest telemetry」(最新遥测)标签页"
        ),
        "expected": (
            "1. 进入设备页面,设备表格出现「HVAC」记录行\n"
            "2. 点击后打开 HVAC 设备详情(出现设备名「HVAC」以及 Details/Attributes/"
            "Latest telemetry 等详情标签页)\n"
            "3. 切到「Latest telemetry」后,出现遥测数据键值行(如 temperature 等指标)"
        ),
    },
]

HEADERS = ["用例编号", "用例名称", "预置条件", "测试步骤", "预期结果"]


def _sub(text: str) -> str:
    """把凭据占位符替换成环境变量提供的真实值(未设则保留占位符,生成模板)。"""
    return text.replace("__TB_EMAIL__", EMAIL).replace("__TB_PASSWORD__", PASSWORD)


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "用例"
    ws.append(HEADERS)
    for c in CASES:
        ws.append(
            [c["id"], c["name"], _sub(c["preconditions"]), _sub(c["steps"]), _sub(c["expected"])]
        )
    out = Path(__file__).with_name("thingsboard_cases.xlsx")
    wb.save(out)
    note = "" if EMAIL.startswith("<") else f"(凭据 {EMAIL})"
    print(f"已生成 {out}({len(CASES)} 条用例){note}")
    if EMAIL.startswith("<"):
        print("⚠️ 未设 TB_EMAIL/TB_PASSWORD,xlsx 内为占位符,跑前需替换或设环境变量重生成。")


if __name__ == "__main__":
    main()
