# -*- coding: utf-8 -*-
"""验证中控侧：内核下载响应头带版本号、上报的内核版本/冲突进程能落库并告警。

在临时目录里跑一份 server.py 副本，避免污染真实库。
用法: python tests/e2e_server_kernel.py
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "server")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, dict(r.getheaders()), raw


def main():
    for s in (sys.stdout, sys.stderr):
        if s is not None and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    tmp = tempfile.mkdtemp(prefix="minrigs_srv_")
    os.makedirs(os.path.join(tmp, "static"))
    os.makedirs(os.path.join(tmp, "data", "kernels"))
    shutil.copy(os.path.join(SRC, "server.py"), tmp)
    shutil.copy(os.path.join(SRC, "static", "index.html"),
                os.path.join(tmp, "static", "index.html"))

    # 预置「已缓存」的内核 zip（>1MB 才认缓存，这里造 1.2MB）
    kz = os.path.join(tmp, "data", "kernels", "srbminer-v9.9.9.zip")
    with zipfile.ZipFile(kz, "w", zipfile.ZIP_STORED) as z:
        z.writestr("SRBMiner-MULTI-9.9.9/SRBMiner-MULTI.exe", b"X" * 1_200_000)
    with open(os.path.join(tmp, "data", "kernel_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"checked": time.time(), "catalog": {
            "srbminer": {"version": "v9.9.9", "url": "https://example.invalid/x.zip",
                         "size": 1_200_000, "file": "srbminer-v9.9.9.zip",
                         "label": "SRBMiner-MULTI", "exe": "SRBMiner-MULTI.exe",
                         "checked": time.time()}}}, f)

    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "server.py"], cwd=tmp,
        env=dict(os.environ, PORT=str(port), NO_PROXY="127.0.0.1,localhost",
                 no_proxy="127.0.0.1,localhost"),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                st, _, _ = req(port, "GET", "/api/health")
                if st == 200:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)

        fails = []

        def check(cond, msg):
            print(("  [OK]   " if cond else "  [FAIL] ") + msg)
            if not cond:
                fails.append(msg)

        # 1) 重启后版本信息还在（load_kernel_meta）
        st, _, raw = req(port, "GET", "/api/kernel_catalog")
        cat = json.loads(raw).get("catalog") or {}
        check(st == 200 and cat.get("srbminer", {}).get("version") == "v9.9.9",
              "重启后内核版本信息已载入（%s）" % cat.get("srbminer", {}).get("version"))

        # 2) kernel_dl 响应头带版本
        st, hd, body = req(port, "GET", "/api/kernel_dl?kernel=srbminer&rig=TESTRIG",
                           headers={"X-Rig-Token": "tk-abc"})
        check(st == 200 and len(body) > 1_000_000, "内核下载返回 %d 字节" % len(body))
        check(hd.get("X-Kernel-Version") == "v9.9.9",
              "响应头 X-Kernel-Version = %s" % hd.get("X-Kernel-Version"))
        check(hd.get("X-Kernel-Key") == "srbminer",
              "响应头 X-Kernel-Key = %s" % hd.get("X-Kernel-Key"))

        # 3) 上报内核版本 + 冲突进程 → 落库 + 告警
        payload = {
            "name": "TESTRIG", "token": "tk-abc", "hostname": "testrig",
            "kernel": "SRBMiner-MULTI", "kernel_ver": "v9.0.0",
            "conflicts": ["XMRig 门罗", "BaPiMiner 重复实例"],
            "gpus": [{"index": 0, "model": "NVIDIA GeForce RTX 4090", "temp": 70,
                      "util": 98, "mem_used": 20000, "mem_total": 24564,
                      "fan": 70, "power": 430}],
            "miners": [{"type": "srbminer", "alive": True, "ths": 421.7,
                        "accepted": 100, "rejected": 1, "uptime": 600,
                        "pool": "prl.kryptex.network:7048"}],
        }
        st, _, raw = req(port, "POST", "/api/report", payload)
        check(st == 200 and json.loads(raw).get("ok"), "上报成功")

        st, _, raw = req(port, "GET", "/api/alerts",
                         headers={"X-Admin-Token": json.load(
                             open(os.path.join(tmp, "config.json"), encoding="utf-8")
                         )["admin_token"]})
        alerts = json.loads(raw).get("alerts") or []
        kinds = {a["kind"] for a in alerts}
        check("foreign_miner" in kinds, "其他挖矿程序已生成告警（%s）" % sorted(kinds))

        tok = json.load(open(os.path.join(tmp, "config.json"), encoding="utf-8"))["admin_token"]
        st, _, raw = req(port, "GET", "/api/fleet", headers={"X-Admin-Token": tok})
        fleet = json.loads(raw)
        rig = next((r for r in fleet["rigs"] if r["name"] == "TESTRIG"), None)
        snap = (rig or {}).get("snapshot") or {}
        check(snap.get("kernel_ver") == "v9.0.0",
              "看板能读到矿机内核版本（%s）" % snap.get("kernel_ver"))
        check(snap.get("conflicts") == ["XMRig 门罗", "BaPiMiner 重复实例"],
              "看板能读到冲突进程列表（%s）" % snap.get("conflicts"))

        # 4) 冲突消失后告警自动恢复
        payload["conflicts"] = []
        payload["kernel_ver"] = "v9.9.9"
        req(port, "POST", "/api/report", payload)
        st, _, raw = req(port, "GET", "/api/alerts", headers={"X-Admin-Token": tok})
        al = [a for a in json.loads(raw)["alerts"] if a["kind"] == "foreign_miner"]
        check(al and al[0]["resolved"] == 1, "冲突消失后告警自动标记已恢复")

        print("\n== e2e_server_kernel", "PASSED ==" if not fails else "FAILED: %d" % len(fails))
        return 0 if not fails else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
