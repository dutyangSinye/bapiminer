# -*- coding: utf-8 -*-
"""回归测试：HTTP/1.1 keep-alive 连接上「未读请求体」导致的 501 串包。

背景：
    浏览器 POST /api/auth/logout 会带 Content-Length: 2 的 body `{}`。
    若服务端该路由没有消费请求体，残留字节会留在 TCP 缓冲区；
    下一个请求复用同一连接时，服务端从 `{}` 开始解析请求行，
    得到非法 method（`{}POST`），BaseHTTPRequestHandler 回 501 + text/html，
    前端 r.json() 抛 SyntaxError。

本测试用 http.client 复用同一个 TCP 连接，先 logout 再 login，
断言第二个请求仍能拿到正常 JSON（而不是 501 HTML）。
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

TMP = tempfile.mkdtemp(prefix="mu-ka-")

# ★ 必须在 import server 之前改好路径：
#   server.py 在模块级就把 DB_FILE = DATA_DIR/fleet.db 定死了，
#   import 之后再改 S.DATA_DIR 已经太晚（连接会连到真实库）。
#   这里用「源码替换 + 落到临时目录再 import」的方式彻底隔离。
SRC = open(os.path.join(ROOT, "server", "server.py"), encoding="utf-8").read()
SRC = SRC.replace('DATA_DIR = os.path.join(BASE_DIR, "data")', 'DATA_DIR = %r' % TMP)
SRC = SRC.replace('CONFIG_FILE = os.path.join(BASE_DIR, "config.json")',
                  'CONFIG_FILE = %r' % os.path.join(TMP, "config.json"))
SRC = SRC.replace('STATIC_DIR = os.path.join(BASE_DIR, "static")',
                  'STATIC_DIR = %r' % os.path.join(ROOT, "server", "static"))
_RUN = os.path.join(TMP, "_srv_under_test.py")
with open(_RUN, "w", encoding="utf-8") as f:
    f.write(SRC)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("_srv_under_test", _RUN)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

# 双保险：确认没连到真实库
assert os.path.abspath(S.DB_FILE).startswith(os.path.abspath(TMP)), \
    "测试未隔离！DB_FILE=%s" % S.DB_FILE

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


def main():
    S.init_db()
    ok, out = S.register_user("kauser", "kapass123")
    check("注册 kauser 成功", ok, out)

    httpd = S.ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)

    print("\n=== ① 同一 TCP 连接：logout（带 body）→ login ===")
    body = json.dumps({})
    conn.request("POST", "/api/auth/logout", body=body,
                 headers={"Content-Type": "application/json",
                          "Content-Length": str(len(body))})
    r1 = conn.getresponse()
    d1 = r1.read().decode("utf-8", "replace")
    check("logout 返回 200", r1.status == 200, "got %s" % r1.status)
    check("logout 返回 JSON", r1.getheader("Content-Type", "").startswith("application/json"),
          r1.getheader("Content-Type"))

    conn.request("POST", "/api/auth/login",
                 body=json.dumps({"username": "kauser", "password": "kapass123"}),
                 headers={"Content-Type": "application/json"})
    r2 = conn.getresponse()
    ct2 = r2.getheader("Content-Type", "")
    d2 = r2.read().decode("utf-8", "replace")
    check("同一连接上的 login 不再是 501", r2.status != 501, "got %s" % r2.status)
    check("login 返回 200", r2.status == 200, "got %s / %s" % (r2.status, d2[:200]))
    check("login Content-Type 是 JSON", ct2.startswith("application/json"), ct2)
    try:
        j2 = json.loads(d2)
        okj = j2.get("ok") and j2.get("token")
    except Exception as e:  # noqa: BLE001
        j2, okj = None, False
        print("    json 解析失败: %s / body=%s" % (e, d2[:200]))
    check("login 返回可用 token", bool(okj))

    print("\n=== ② 连续 3 次 logout/login 交替（同一连接） ===")
    tok = (j2 or {}).get("token", "")
    bad = 0
    for i in range(3):
        conn.request("POST", "/api/auth/logout", body=body,
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(len(body))})
        rr = conn.getresponse()
        rr.read()
        conn.request("POST", "/api/auth/login",
                     body=json.dumps({"username": "kauser", "password": "kapass123"}),
                     headers={"Content-Type": "application/json"})
        rr2 = conn.getresponse()
        dd = rr2.read().decode("utf-8", "replace")
        if rr2.status != 200 or not dd.strip().startswith("{"):
            bad += 1
            print("    第 %d 轮异常: %s %s" % (i + 1, rr2.status, dd[:120]))
        try:
            jj = json.loads(dd)
            tok = jj.get("token", tok)
        except Exception:  # noqa: BLE001
            pass
    check("3 轮交替全部正常", bad == 0, "bad=%d" % bad)

    print("\n=== ③ 无 body 的 POST（rig_logout / kernel/check 类） ===")
    conn.request("POST", "/api/rig_logout", body=body,
                 headers={"Content-Type": "application/json",
                          "Content-Length": str(len(body))})
    r3 = conn.getresponse()
    d3 = r3.read().decode("utf-8", "replace")
    check("rig_logout 返回 200", r3.status == 200, "got %s" % r3.status)

    conn.request("POST", "/api/kernel/check", body=body,
                 headers={"Content-Type": "application/json",
                          "Content-Length": str(len(body)),
                          "X-User-Token": tok})
    r4 = conn.getresponse()
    d4 = r4.read().decode("utf-8", "replace")
    check("kernel/check 不因残留 body 变 501", r4.status != 501, "got %s" % r4.status)
    check("kernel/check 返回 JSON", d4.strip().startswith("{"), d4[:120])

    print("\n=== ④ 之后普通 login 仍正常 ===")
    conn.request("POST", "/api/auth/login",
                 body=json.dumps({"username": "kauser", "password": "kapass123"}),
                 headers={"Content-Type": "application/json"})
    r5 = conn.getresponse()
    d5 = r5.read().decode("utf-8", "replace")
    check("最终 login 返回 200", r5.status == 200, "got %s / %s" % (r5.status, d5[:200]))

    conn.close()
    httpd.shutdown()

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (PASS, FAIL))
    if FAIL == 0:
        print("全部通过")
    else:
        print("存在失败用例")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(rc)
