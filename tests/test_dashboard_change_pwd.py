# -*- coding: utf-8 -*-
"""中控「修改密码」功能的端到端测试。

覆盖：
  1) 已登录用户带 X-User-Token 自助改密（服务端已支持的分支）
  2) 原密码错误 → 拒绝
  3) 新密码太短 → 拒绝
  4) 新旧密码相同 → 拒绝
  5) 改密后旧密码不能登录、新密码能登录
  6) 改密后登录态不受影响（无需重新登录）
  7) 未登录（无 token）→ 拒绝
  8) 前端 index.html 契约（按钮存在、弹窗存在、调用带 token）
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INDEX = os.path.join(ROOT, "server", "static", "index.html")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s%s" % (name, ("  — " + detail) if detail else ""))


def req(url, method="GET", body=None, headers=None):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {}


BASE = os.environ.get("MU_BASE", "http://127.0.0.1:18945")
U = "pwdtest_user"
OLD = "oldPass2026"
NEW = "newPass2026"

print("=" * 74)
print("1. 注册测试账号")
print("=" * 74)
st, d = req(BASE + "/api/auth/register", "POST",
            {"username": U, "password": OLD})
print("   register → %s %s" % (st, d))
check("注册成功或账号已存在", d.get("ok") is True or "已存在" in str(d.get("error")))

print()
print("=" * 74)
print("2. 登录拿 token")
print("=" * 74)
st, d = req(BASE + "/api/auth/login", "POST", {"username": U, "password": OLD})
check("登录成功", d.get("ok") is True and d.get("token"),
      str(d)[:150])
TOK = d.get("token") or ""
if not TOK:
    print("   无法登录，后续用例跳过")
    sys.exit(1)

print()
print("=" * 74)
print("3. 自助改密（带 X-User-Token）")
print("=" * 74)
st, d = req(BASE + "/api/auth/change_password", "POST",
            {"old_password": OLD, "new_password": NEW},
            {"X-User-Token": TOK})
check("带 token 改密成功", d.get("ok") is True, "%s %s" % (st, str(d)[:150]))

print()
print("=" * 74)
print("4. 改密后：旧密码失效 / 新密码可登录 / 登录态不受影响")
print("=" * 74)
st, d = req(BASE + "/api/auth/login", "POST", {"username": U, "password": OLD})
check("旧密码已不能登录", d.get("ok") is not True and not d.get("token"),
      str(d)[:120])

st, d = req(BASE + "/api/auth/login", "POST", {"username": U, "password": NEW})
check("新密码可以登录", d.get("ok") is True and d.get("token"), str(d)[:120])

st, d = req(BASE + "/api/auth/me", "GET", None, {"X-User-Token": TOK})
check("改密后原 token 仍然有效（无需重新登录）",
      d.get("ok") is True and d.get("user", {}).get("username") == U,
      str(d)[:150])

print()
print("=" * 74)
print("5. 错误输入必须被拒绝")
print("=" * 74)
st, d = req(BASE + "/api/auth/change_password", "POST",
            {"old_password": "wrongPass", "new_password": "aaa111"},
            {"X-User-Token": TOK})
check("原密码错误 → 拒绝", d.get("ok") is not True, str(d)[:120])

st, d = req(BASE + "/api/auth/change_password", "POST",
            {"old_password": NEW, "new_password": "abc"},
            {"X-User-Token": TOK})
check("新密码太短 → 拒绝", d.get("ok") is not True, str(d)[:120])

st, d = req(BASE + "/api/auth/change_password", "POST",
            {"old_password": NEW, "new_password": NEW},
            {"X-User-Token": TOK})
check("新密码与原密码相同 → 拒绝", d.get("ok") is not True, str(d)[:120])

st, d = req(BASE + "/api/auth/change_password", "POST",
            {"old_password": NEW, "new_password": "zzz999999"})
check("无 token 且无 username → 拒绝", d.get("ok") is not True, str(d)[:120])

st, d = req(BASE + "/api/auth/change_password", "POST",
            {"old_password": NEW, "new_password": "zzz999999"},
            {"X-User-Token": "invalid-token-xxx"})
check("无效 token → 拒绝", d.get("ok") is not True, str(d)[:120])

print()
print("=" * 74)
print("6. 前端 index.html 契约")
print("=" * 74)
src = open(INDEX, encoding="utf-8").read()
check("顶栏有「修改密码」按钮", 'onclick="openPwdModal()"' in src)
check("弹窗容器存在", 'id="pwdModal"' in src)
check("弹窗含当前密码输入", 'id="mpOld"' in src)
check("弹窗含新密码输入", 'id="mpNew"' in src)
check("弹窗含确认密码输入", 'id="mpNew2"' in src)
check("提交函数存在", 'function submitMyPwdChange' in src)
check("调用 /api/auth/change_password", "'/api/auth/change_password'" in src)
check("请求带 X-User-Token",
      re.search(r"change_password[\s\S]{0,400}?X-User-Token", src) is not None)
check("关闭函数存在", 'function closePwdModal' in src)
check("Esc 可关闭弹窗", "e.key === 'Escape'" in src)
check("点击遮罩可关闭", "e.target === m" in src)
check("提示改密成功后需用新密码登录",
      "下次登录请使用新密码" in src)
check("两处新密码一致性校验", "两次输入的新密码不一致" in src)
check("新密码长度校验", "新密码至少 6 位" in src)
check("新旧密码相同校验", "新密码不能与当前密码相同" in src)

print()
print("=" * 74)
print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
