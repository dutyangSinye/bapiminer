# -*- coding: utf-8 -*-
"""验证中控分发客户端安装包：清单 / 完整下载 / Range 续传 / 路径穿越拦截 / 未上传提示。

在临时目录跑 server.py 副本，data/downloads 里放假安装包。
用法: python tests/e2e_downloads.py
"""
import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "server")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def req(port, method, path, headers=None, timeout=30):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request(method, path, headers=dict(headers or {}))
    r = c.getresponse()
    raw = r.read()
    hd = dict(r.getheaders())
    c.close()
    return r.status, hd, raw


def main():
    for s in (sys.stdout, sys.stderr):
        if s is not None and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    tmp = tempfile.mkdtemp(prefix="minrigs_dl_")
    os.makedirs(os.path.join(tmp, "static"))
    dl_dir = os.path.join(tmp, "data", "downloads")
    os.makedirs(dl_dir)
    shutil.copy(os.path.join(SRC, "server.py"), tmp)
    shutil.copy(os.path.join(SRC, "static", "index.html"),
                os.path.join(tmp, "static", "index.html"))

    # 假 exe 1.5MB（含可校验的随机内容），apk 故意不放，验证「未上传」分支
    exe_path = os.path.join(dl_dir, "BaPiMiner.exe")
    blob = (b"MINRIGS-EXE" + os.urandom(1)) * 120_000
    with open(exe_path, "wb") as f:
        f.write(blob)
    exe_sha = hashlib.sha256(blob).hexdigest()
    exe_size = len(blob)

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "server.py"], cwd=tmp,
        env=dict(os.environ, PORT=str(port), NO_PROXY="127.0.0.1,localhost",
                 no_proxy="127.0.0.1,localhost"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    fails = []

    def check(cond, msg):
        print(("  [OK]   " if cond else "  [FAIL] ") + msg)
        if not cond:
            fails.append(msg)

    try:
        for _ in range(60):
            try:
                st, _, _ = req(port, "GET", "/api/health")
                if st == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)

        # 1) 清单
        st, _, raw = req(port, "GET", "/api/downloads")
        d = json.loads(raw) if st == 200 else {}
        items = {i["key"]: i for i in (d.get("items") or [])}
        check(st == 200 and len(items) == 2, "清单返回 %d 个安装包条目" % len(items))
        exe = items.get("exe") or {}
        apk = items.get("apk") or {}
        check(exe.get("ready") and exe.get("size") == exe_size,
              "exe 条目 size=%s（实际 %d）" % (exe.get("size"), exe_size))
        check(exe.get("sha256") == exe_sha, "exe sha256 正确")
        check(exe.get("url") == "/download/BaPiMiner.exe", "exe 下载链接 %s" % exe.get("url"))
        check(apk and apk.get("ready") is False, "apk 未上传时标记 ready=false")

        # 2) 完整下载
        st, hd, body = req(port, "GET", "/download/BaPiMiner.exe", timeout=60)
        check(st == 200 and len(body) == exe_size, "完整下载 %d 字节" % len(body))
        check(hashlib.sha256(body).hexdigest() == exe_sha, "下载内容 sha256 与清单一致")
        check("attachment" in (hd.get("Content-Disposition") or ""), "带 attachment 下载头")
        check(hd.get("Accept-Ranges") == "bytes", "声明支持 Range 续传")

        # 3) Range 续传
        st, hd, part = req(port, "GET", "/download/BaPiMiner.exe",
                           headers={"Range": "bytes=0-99"})
        check(st == 206 and len(part) == 100, "Range 0-99 -> %d 字节" % len(part))
        check(part == blob[:100], "Range 返回内容正确")
        check((hd.get("Content-Range") or "").startswith("bytes 0-99/"),
              "Content-Range=%s" % hd.get("Content-Range"))
        st, hd, part2 = req(port, "GET", "/download/BaPiMiner.exe",
                            headers={"Range": "bytes=%d-" % (exe_size - 10)})
        check(st == 206 and len(part2) == 10 and part2 == blob[-10:], "尾部 Range 正确")
        st, _, _ = req(port, "GET", "/download/BaPiMiner.exe",
                       headers={"Range": "bytes=%d-" % (exe_size + 5)})
        check(st == 416, "越界 Range 返回 416（实际 %d）" % st)

        # 4) 未上传 / 非法文件名
        st, _, _ = req(port, "GET", "/download/BaPiMiner-android.apk")
        check(st == 404, "未上传的 apk 返回 404（实际 %d）" % st)
        for bad in ("/download/..%2Fserver.py", "/download/config.json", "/download/../server.py"):
            st, _, _ = req(port, "GET", bad)
            check(st == 404, "拒绝 %s（%d）" % (bad, st))

        # 5) 看板页面仍正常
        st, _, page = req(port, "GET", "/")
        html = page.decode("utf-8", "replace")
        check(st == 200 and "客户端下载" in html and "dlList" in html, "看板包含下载区块")

        print("== e2e downloads", "PASSED ==" if not fails else "FAILED ==")
        return 0 if not fails else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
