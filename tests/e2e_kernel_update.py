# -*- coding: utf-8 -*-
"""端到端验证：中控下发 update_kernel 后，矿机端的内核文件是否真被替换。

起一个假的「中控」HTTP 服务，按需返回不同版本的内核 zip + X-Kernel-Version 头，
然后走矿机端的 ensure_kernel / _handle_cmd 真实路径。

用法: python tests/e2e_kernel_update.py
"""
import http.server
import io
import os
import queue
import sys
import tempfile
import threading
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "client"))
import miner_client as mc  # noqa: E402

STATE = {"version": "v2.0.0", "body": "EXE_V1"}
HITS = []


def make_zip(body, version):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SRBMiner-MULTI-%s/SRBMiner-MULTI.exe" % version, body)
        z.writestr("SRBMiner-MULTI-%s/kernel.cl" % version, "cl")
    return buf.getvalue()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if not self.path.startswith("/api/kernel_dl"):
            self.send_response(404)
            self.end_headers()
            return
        HITS.append(self.path)
        data = make_zip(STATE["body"], STATE["version"])
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Kernel-Version", STATE["version"])
        self.send_header("X-Kernel-Name", "srb-test.zip")
        self.end_headers()
        self.wfile.write(data)


def main():
    for s in (sys.stdout, sys.stderr):
        if s is not None and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % port
    kd = tempfile.mkdtemp(prefix="minrigs_knl_")
    exe = os.path.join(kd, "SRBMiner-MULTI.exe")
    print("假中控 %s  内核目录 %s" % (url, kd))

    fails = []

    def check(cond, msg):
        print(("  [OK]   " if cond else "  [FAIL] ") + msg)
        if not cond:
            fails.append(msg)

    # 1) 首次安装
    err, v = mc.ensure_kernel("SRBMiner-MULTI", kd, url, "tok", rig_name="t1")
    check(err is None and v == "v2.0.0", "首次安装拿到版本 %s（err=%s）" % (v, err))
    check(os.path.isfile(exe) and open(exe, encoding="utf-8").read() == "EXE_V1",
          "首次安装后 exe 内容 = EXE_V1")

    # 2) exe 已存在且不带 force：应原样保留（这是旧版「下发更新没生效」的行为，必须可区分）
    err, v = mc.ensure_kernel("SRBMiner-MULTI", kd, url, "tok", rig_name="t1")
    check(open(exe, encoding="utf-8").read() == "EXE_V1",
          "不带 force 时不会动已有内核（旧行为，用于兼容启动兜底）")

    # 3) 中控发布新版本 → force 更新
    STATE["version"] = "v2.0.1"
    STATE["body"] = "EXE_V2"
    before = len(HITS)
    err, v = mc.ensure_kernel("SRBMiner-MULTI", kd, url, "tok", rig_name="t1", force=True)
    check(err is None and v == "v2.0.1", "force 更新返回新版本 %s（err=%s）" % (v, err))
    check(open(exe, encoding="utf-8").read() == "EXE_V2", "内核文件内容已被真实替换为 EXE_V2")
    check(len(HITS) == before + 1, "确实向中控发起了下载请求")
    check(mc.kernel_local_version("SRBMiner-MULTI", kd) == "v2.0.1",
          "版本号已落地：%s" % mc.kernel_local_version("SRBMiner-MULTI", kd))
    check(not os.path.exists(exe + ".old"), "替换后没有残留 .old 文件")

    # 4) 同版本再 force：不重复覆盖
    before = len(HITS)
    err, v = mc.ensure_kernel("SRBMiner-MULTI", kd, url, "tok", rig_name="t1", force=True)
    check(v == "v2.0.1" and open(exe, encoding="utf-8").read() == "EXE_V2",
          "同版本 force 保持 v2.0.1 且文件不变")

    # 5) 走中控命令路径：update_kernel
    eng = mc.MiningEngine({
        "kernel": "SRBMiner-MULTI", "kernel_dir": kd, "server": url, "token": "tok",
        "rig_name": "t1", "algo": "PRL (Pearl)",
        "pool_url": "stratum+tcp://x:1", "wallet": "w", "worker": "w",
    }, queue.Queue())
    ok, note = eng._handle_cmd("update_kernel")
    check(ok and "已是" in note, "同版本下发回执：%s" % note)

    STATE["version"] = "v2.0.2"
    STATE["body"] = "EXE_V3"
    ok, note = eng._handle_cmd("update_kernel")
    check(ok and "v2.0.1" in note and "v2.0.2" in note, "跨版本下发回执：%s" % note)
    check(open(exe, encoding="utf-8").read() == "EXE_V3", "命令执行后 exe 内容 = EXE_V3")

    # 6) 离线（中控不可达）但本地已有内核：不报错
    err, v = mc.ensure_kernel("SRBMiner-MULTI", kd, url, "tok", force=False)
    check(err is None and v == "v2.0.2", "本地已有内核时离线仍可用（v=%s）" % v)

    srv.shutdown()
    print("\n== e2e_kernel_update", "PASSED ==" if not fails else "FAILED: %d" % len(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
