# -*- coding: utf-8 -*-
"""「部署预置管理员 → 首次登录强制改密」专项测试。

流程：
  部署时预置 admin（临时密码）→ must_change_pwd=1
  首次登录 → 服务端返回 need_password_change，不发 token
  用旧密码换新密码 → 标记清除
  用新密码登录 → 正常拿到 token，进入系统
  旧密码失效 / 弱密码被拒 / 改密前拿不到 token
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

TMP = tempfile.mkdtemp(prefix="mu-pwd-")
os.environ["MINRIGS_DATA_DIR"] = TMP
os.environ["MINRIGS_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["MINRIGS_STATIC_DIR"] = os.path.join(ROOT, "server", "static")

import server as S  # noqa: E402

S.DATA_DIR = TMP
S.DB_FILE = os.path.join(TMP, "test.db")
S.CONFIG_FILE = os.path.join(TMP, "config.json")
S._load_config()
S.init_db()

OK = 0
FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s   %s" % (name, extra))


PORT = 18778
srv = ThreadingHTTPServer(("127.0.0.1", PORT), S.Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()


def api(path, method="GET", body=None, user_token=None, rig_token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path),
                                 data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if user_token:
        req.add_header("X-User-Token", user_token)
    if rig_token:
        req.add_header("X-Rig-Token", rig_token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": str(e)}


print("=== ① 部署预置 admin，标记 must_change_pwd ===")
S._insert_user("admin", "TempPass123", is_admin=1, must_change_pwd=1)
row = S.db().execute("SELECT must_change_pwd FROM users WHERE username='admin'").fetchone()
check("预置后 must_change_pwd=1", int(row["must_change_pwd"]) == 1, row)
pub = S.user_public(S.user_by_name("admin"))
check("user_public 带 must_change_pwd 标志", pub.get("must_change_pwd") is True, pub)
check("user_public 不含密码字段",
      "pwd_hash" not in pub and "pwd_salt" not in pub, pub)

print("\n=== ② 首次登录：要求改密且不发 token ===")
st, r = api("/api/auth/login", "POST",
            {"username": "admin", "password": "TempPass123"})
check("登录返回 200", st == 200, st)
check("need_password_change=True", r.get("need_password_change") is True, r)
check("不返回 token", not r.get("token"), r)

print("\n=== ③ 改密前拿不到受保护资源 ===")
st, r = api("/api/fleet")
check("未登录 /api/fleet → 401", st == 401, st)

print("\n=== ④ 改密：旧密码错误被拒 ===")
st, r = api("/api/auth/change_password", "POST",
            {"username": "admin", "old_password": "WrongPass",
             "new_password": "NewPass456"})
check("旧密码错 → 401", st == 401, st)

print("\n=== ⑤ 改密：弱密码被拒 / 新旧相同被拒 ===")
st, r = api("/api/auth/change_password", "POST",
            {"username": "admin", "old_password": "TempPass123",
             "new_password": "123"})
check("弱密码被拒 → 400", st == 400, st)
st, r = api("/api/auth/change_password", "POST",
            {"username": "admin", "old_password": "TempPass123",
             "new_password": "TempPass123"})
check("新旧相同被拒 → 400", st == 400, st)

print("\n=== ⑥ 改密成功 → 标记清除 ===")
st, r = api("/api/auth/change_password", "POST",
            {"username": "admin", "old_password": "TempPass123",
             "new_password": "BrandNewPass456"})
check("改密成功", st == 200 and r.get("ok"), r)
row = S.db().execute("SELECT must_change_pwd FROM users WHERE username='admin'").fetchone()
check("must_change_pwd 已清零", int(row["must_change_pwd"]) == 0, row)

print("\n=== ⑦ 用新密码登录 → 正常拿 token ===")
st, r = api("/api/auth/login", "POST",
            {"username": "admin", "password": "BrandNewPass456"})
check("新密码登录成功", st == 200 and r.get("ok"), r)
check("拿到 token", bool(r.get("token")), r)
check("不再要求改密", not r.get("need_password_change"), r)
tok = r.get("token")
st, r = api("/api/fleet", user_token=tok)
check("可用 token 访问受保护接口", st == 200, st)

print("\n=== ⑧ 旧密码失效 ===")
st, r = api("/api/auth/login", "POST",
            {"username": "admin", "password": "TempPass123"})
check("旧密码登录被拒 → 401", st == 401, st)

print("\n=== ⑨ 已登录用户自助改密（走 token）===")
st, r = api("/api/auth/change_password", "POST",
            {"old_password": "BrandNewPass456", "new_password": "ThirdPass7890"},
            user_token=tok)
check("自助改密成功", st == 200 and r.get("ok"), r)
st, r = api("/api/auth/login", "POST",
            {"username": "admin", "password": "ThirdPass7890"})
check("第三次密码可登录", st == 200 and r.get("token"), r)
st, r = api("/api/auth/change_password", "POST",
            {"old_password": "wrong", "new_password": "Xxxx123456"},
            user_token=tok)
check("token 态下旧密码错 → 400/401", st in (400, 401), st)

print("\n=== ⑩ 矿机端登录同样受改密约束 ===")
# 造一台矿机
api("/api/report", "POST", {"name": "rig-p", "token": "tok-p",
                             "display_name": "rig-p"})
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-p", "username": "admin", "password": "ThirdPass7890"},
            rig_token="tok-p")
check("正常账号可在矿机端登录", st == 200 and r.get("ok"), r)
# 新建一个待改密账号
S._insert_user("tmpadm", "Init123456", is_admin=1, must_change_pwd=1)
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-p", "username": "tmpadm", "password": "Init123456"},
            rig_token="tok-p")
check("待改密账号矿机端登录 → need_password_change",
      r.get("need_password_change") is True, r)
check("待改密时不发 token", not r.get("token"), r)
owner = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-p'").fetchone()["owner_id"]
check("待改密时不做归属绑定", int(owner or 0) != S.user_by_name("tmpadm")["id"], owner)

srv.shutdown()
print("\n" + "=" * 50)
print("PASS %d / FAIL %d" % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
