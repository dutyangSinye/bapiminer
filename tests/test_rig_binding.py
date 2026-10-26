# -*- coding: utf-8 -*-
"""归属产生方式的专项测试（无主矿机接入模式）。

核心语义（取代原「批量认领」设计）：
  · 无主矿机（owner_id=0）可以直接接入中控、上报数据
  · 归属**只能**由矿机端首次登录产生（/api/rig_login 自动绑定）
  · 不存在任何 admin 手工指定归属的接口（rig_claim / rig_bind / rig_rebind 全部 404）
  · 无主矿机只对 admin 可见，普通用户不可见（D5：不支持多用户只读共享）
  · 归属转移的唯一合法路径：原属主解绑 → 变无主 → 新用户在矿机端登录绑定
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

TMP = tempfile.mkdtemp(prefix="mu-bind-")
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


def api(path, method="GET", body=None, user_token=None, rig_token=None,
        admin_token=None):
    import json
    import urllib.request
    import urllib.error
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path),
                                 data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if user_token:
        req.add_header("X-User-Token", user_token)
    if rig_token:
        req.add_header("X-Rig-Token", rig_token)
    if admin_token:
        req.add_header("X-Admin-Token", admin_token)
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


# 起一个真实 HTTP 服务（走完整路由，不是直接调函数）
import threading  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

PORT = 18777
srv = ThreadingHTTPServer(("127.0.0.1", PORT), S.Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

# 造数据
S._insert_user("alice", "alice123", is_admin=0)
S._insert_user("bob", "bob12345", is_admin=0)
S._insert_user("boss", "boss123456", is_admin=1)

# 两台矿机先上报（成为无主矿机）
for rig in ("rig-x", "rig-y"):
    api("/api/report", "POST", {
        "name": rig, "token": "tok-" + rig, "display_name": rig + "-机器",
        "gpus": [{"index": 0, "model": "RTX 5080", "temp": 45, "fan": 40,
                  "power": 180, "ths": 1.2}],
        "miners": [{"type": "srbminer", "ths": 1.2, "accepted": 3, "rejected": 0,
                    "uptime": 30, "alive": True,
                    "pool": "stratum+tcp://prl-hk.kryptex.network:7048"}]})

alice_uid = S.user_by_name("alice")["id"]
bob_uid = S.user_by_name("bob")["id"]

print("=== ① 无主矿机可直接接入（上报无需归属）===")
for rig in ("rig-x", "rig-y"):
    row = S.db().execute("SELECT owner_id FROM rigs WHERE name=?", (rig,)).fetchone()
    check("%s 上报后为无主（owner_id=0）" % rig, int(row["owner_id"] or 0) == 0, row)
st, r = api("/api/fleet", admin_token=S._admin_token)
check("admin 能看到两台无主矿机", len(r.get("rigs", [])) == 2, len(r.get("rigs", [])))
check("fleet 返回 owner_id=0 标记",
      all(x["owner_id"] == 0 for x in r.get("rigs", [])), r.get("rigs"))

print("\n=== ② 无主矿机对普通用户不可见（D5 不共享）===")
st, r = api("/api/auth/login", "POST", {"username": "alice", "password": "alice123"})
alice_tok = r.get("token")
st, r = api("/api/fleet", user_token=alice_tok)
check("alice 看不到无主矿机", len(r.get("rigs", [])) == 0, len(r.get("rigs", [])))

print("\n=== ③ admin 手工指定归属的接口已全部移除 ===")
for p, body in (("/api/rig_claim", {"username": "alice", "rigs": ["rig-x"]}),
                 ("/api/rig_bind", {"rig": "rig-x", "username": "alice"}),
                 ("/api/rig_rebind", {"rig": "rig-x", "username": "alice"}),
                 ("/api/unclaimed_rigs", None)):
    st, r = api(p, "POST" if body else "GET", body, admin_token=S._admin_token)
    check("%s → 404" % p, st == 404, st)
row = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-x'").fetchone()
check("rig-x 仍是无主（没被偷偷绑定）", int(row["owner_id"] or 0) == 0, row)

print("\n=== ④ 矿机端首次登录自动绑定 ===")
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-x", "username": "alice", "password": "alice123"},
            rig_token="tok-rig-x")
check("alice 在矿机端登录成功", st == 200 and r.get("ok"), r)
row = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-x'").fetchone()
check("rig-x 自动绑定到 alice", row["owner_id"] == alice_uid, row)
st, r = api("/api/fleet", user_token=alice_tok)
check("alice 现在看到 1 台", len(r.get("rigs", [])) == 1, len(r.get("rigs", [])))
st, r = api("/api/fleet", admin_token=S._admin_token)
x = [y for y in r["rigs"] if y["name"] == "rig-x"][0]
check("fleet 带出 owner_name=alice", x.get("owner_name") == "alice", x)

print("\n=== ⑤ 重复登录幂等 ===")
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-x", "username": "alice", "password": "alice123"},
            rig_token="tok-rig-x")
check("重复登录成功（幂等）", st == 200 and r.get("ok"), r)
row = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-x'").fetchone()
check("归属未变", row["owner_id"] == alice_uid, row)

print("\n=== ⑥ 他人矿机：已绑定则拒绝（不再允许 admin 强制抢走）===")
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-x", "username": "bob", "password": "bob12345"},
            rig_token="tok-rig-x")
check("bob 登录 alice 的矿机 → 409", st == 409, st)
check("提示原属主", "alice" in str(r.get("error", "")), r)
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-x", "username": "boss", "password": "boss123456"},
            rig_token="tok-rig-x")
check("admin 登录也不能抢走 → 409", st == 409, st)
row = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-x'").fetchone()
check("归属仍属 alice", row["owner_id"] == alice_uid, row)

print("\n=== ⑦ 归属转移唯一路径：解绑 → 无主 → 重新登录绑定 ===")
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-x", "username": "alice", "password": "alice123"},
            rig_token="tok-rig-x")
st, r = api("/api/rig_unbind", "POST", {"rig": "rig-x"}, user_token=alice_tok)
check("alice 解绑成功", st == 200 and r.get("ok"), r)
row = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-x'").fetchone()
check("rig-x 变回无主", int(row["owner_id"] or 0) == 0, row)
st, r = api("/api/fleet", user_token=alice_tok)
check("解绑后 alice 看不到它", len(r.get("rigs", [])) == 0, len(r.get("rigs", [])))
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-x", "username": "bob", "password": "bob12345"},
            rig_token="tok-rig-x")
check("bob 在矿机端登录 → 绑定", st == 200 and r.get("ok"), r)
row = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-x'").fetchone()
check("rig-x 现属 bob", row["owner_id"] == bob_uid, row)
st, r = api("/api/fleet", user_token=alice_tok)
check("alice 已看不到 rig-x", len(r.get("rigs", [])) == 0, len(r.get("rigs", [])))

print("\n=== ⑧ admin 也能解绑（应急）===")
st, r = api("/api/rig_unbind", "POST", {"rig": "rig-y"}, admin_token=S._admin_token)
check("admin 解绑无主矿机（幂等）", st == 200 and r.get("ok"), r)

print("\n=== ⑨ 审计只记归属变更 ===")
st, r = api("/api/owner_log", admin_token=S._admin_token)
acts = [x["action"] for x in r.get("log", [])]
check("审计含 bind", "bind" in acts, acts)
check("审计含 unbind", "unbind" in acts, acts)
check("审计无 admin_force（无手工指定归属）", "admin_force" not in acts, acts)
check("审计无 rebind", "rebind" not in acts, acts)

srv.shutdown()
print("\n" + "=" * 50)
print("PASS %d / FAIL %d" % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
