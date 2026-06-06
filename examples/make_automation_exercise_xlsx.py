"""生成 Automation Exercise 业务用例 Excel(P4 真实环境验证素材)。

为什么选 Automation Exercise(https://automationexercise.com):
- 开源练习站,公开发布了 26 条**业务语言**测试用例(/test_cases),自带「步骤 + 预期结果」,
  正好贴合本平台 Excel 入口(用例名称/预置条件/测试步骤/预期结果)。
- 流程比 saucedemo 复杂:注册(多字段表单)、登录后下单(加购→结算→填支付→下单)、
  搜索加购等,能压测 TestSpec 翻译、断言翻译、词汇表、预置条件分类(P1)等更多链路。
- 无需私密凭据(注册用一次性邮箱即可),适合公开复现。

用法:
    python examples/make_automation_exercise_xlsx.py
    python cli/run_case.py --excel examples/automation_exercise_cases.xlsx \\
        --case-id AE01 --base-url https://automationexercise.com --isolated --headless

注:用例文本基于该站公开的 Test Cases 改写为中文业务描述,仅作演示/验证用途。
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

# 一行一个用例:编号 / 名称 / 预置条件 / 测试步骤 / 预期结果。
# 步骤、预期、预置条件用换行分条;预置条件混合「状态声明」与「操作步骤」以演示 P1 三分类。
CASES = [
    {
        "id": "AE01",
        "name": "注册新用户",
        "preconditions": "1. 浏览器已打开\n2. 使用一个未注册过的邮箱",
        "steps": (
            "1. 打开首页 https://automationexercise.com\n"
            "2. 点击顶部「Signup / Login」\n"
            "3. 在「New User Signup!」下填写姓名 与 邮箱\n"
            "4. 点击「Signup」按钮\n"
            "5. 在账户信息页选择称谓 Mr,填写密码\n"
            "6. 选择出生日期(日/月/年)\n"
            "7. 填写名、姓、地址、国家、州、城市、邮编、手机号\n"
            "8. 点击「Create Account」\n"
            "9. 注册成功后点击「Continue」"
        ),
        "expected": (
            "1. 进入账户信息页时显示「Enter Account Information」\n"
            "2. 创建后显示「Account Created!」\n"
            "3. Continue 后页面顶部显示「Logged in as」用户名"
        ),
    },
    {
        "id": "AE02",
        "name": "登录后下单购买商品",
        "preconditions": "1. 已注册账号(已登录系统)\n2. 购物车为空",
        "steps": (
            "1. 打开首页 https://automationexercise.com\n"
            "2. 点击顶部「Products」进入商品列表\n"
            "3. 将第一个商品「Add to cart」加入购物车\n"
            "4. 在弹窗点击「View Cart」\n"
            "5. 点击「Proceed To Checkout」\n"
            "6. 在地址确认页点击「Place Order」\n"
            "7. 填写支付信息:持卡人姓名、卡号、CVC、过期月、过期年\n"
            "8. 点击「Pay and Confirm Order」"
        ),
        "expected": (
            "1. 购物车页显示已加入的商品行\n"
            "2. 下单后显示「Order Placed!」或「Congratulations! Your order has been confirmed!」"
        ),
    },
    {
        "id": "AE03",
        "name": "搜索商品并加入购物车校验",
        "preconditions": "1. 浏览器已打开",
        "steps": (
            "1. 打开首页 https://automationexercise.com\n"
            "2. 点击顶部「Products」\n"
            "3. 在搜索框输入「dress」并点击搜索按钮\n"
            "4. 将搜索结果中的第一个商品加入购物车\n"
            "5. 点击「View Cart」查看购物车"
        ),
        "expected": ("1. 搜索后显示「Searched Products」标题\n" "2. 购物车中商品数量为 1"),
    },
]

HEADERS = ["用例编号", "用例名称", "预置条件", "测试步骤", "预期结果"]


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "用例"
    ws.append(HEADERS)
    for c in CASES:
        ws.append([c["id"], c["name"], c["preconditions"], c["steps"], c["expected"]])
    out = Path(__file__).with_name("automation_exercise_cases.xlsx")
    wb.save(out)
    print(f"已生成 {out}({len(CASES)} 条用例)")


if __name__ == "__main__":
    main()
