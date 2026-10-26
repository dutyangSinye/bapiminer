# -*- coding: utf-8 -*-
"""把矿机端 exe / 安卓 apk 上传到 NAS，让中控看板可以直接分发下载。

用法: python deploy_client_pkg.py
产物落在 NAS 的 <项目>/data/downloads/，容器内即 /app/data/downloads。
"""
import os
import shutil
import sys
import tempfile
import time

import paramiko

HOST, PORT = "bapimine.click", 10000
USER, PW = "13301116664", "YYyy..83"
REMOTE_DIR = "/data_7HKERMAF/minrigs"
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (本地路径, 远端文件名)
FILES = [
    (os.path.join(PROJ, "client", "dist", "BaPiMiner.exe"), "BaPiMiner.exe"),
    (os.path.join(PROJ, "dist", "扒皮矿工.apk"), "BaPiMiner-android.apk"),
]


def run(cli, cmd, timeout=180, quiet=False):
    _i, o, e = cli.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    rc = o.channel.recv_exit_status()
    if not quiet:
        if out.strip():
            print(out.rstrip())
        tail = [l for l in err.strip().splitlines() if "password" not in l.lower()]
        if tail:
            print("[stderr]", "\n".join(tail[-4:]))
    return rc, out, err


def main():
    for s in (sys.stdout, sys.stderr):
        if s is not None and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    missing = [p for p, _n in FILES if not os.path.isfile(p)]
    if missing:
        print("缺少安装包：\n  " + "\n  ".join(missing))
        return 1

    tmpdir = tempfile.mkdtemp(prefix="minrigs_pkg_")
    staging = []
    for lp, name in FILES:
        sp = os.path.join(tmpdir, name)
        shutil.copy2(lp, sp)
        staging.append((sp, name, os.path.getsize(lp)))
        print("准备上传 %-24s %6.2f MB" % (name, os.path.getsize(lp) / 1048576.0))

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, PORT, USER, PW, timeout=30, banner_timeout=30)
    print("--- 建目录 ---")
    run(cli, "echo '%s' | sudo -S -p '' mkdir -p %s/data/downloads && "
             "echo '%s' | sudo -S -p '' chmod 755 %s/data/downloads"
             % (PW, REMOTE_DIR, PW, REMOTE_DIR))

    sftp = cli.open_sftp()
    try:
        for sp, name, size in staging:
            t0 = time.time()
            remote_tmp = "/tmp/" + name
            print("上传 %s → %s ..." % (name, remote_tmp), flush=True)
            sftp.put(sp, remote_tmp)
            dt = max(0.1, time.time() - t0)
            print("  完成 %.1f MB / %.1fs (%.2f MB/s)"
                  % (size / 1048576.0, dt, size / 1048576.0 / dt))
    finally:
        sftp.close()

    print("--- 落到 data/downloads ---")
    names = " ".join(n for _s, n, _z in staging)
    run(cli, "echo '%s' | sudo -S -p '' sh -c 'cp -f /tmp/%s %s/data/downloads/ && "
             "chmod 644 %s/data/downloads/* && ls -l %s/data/downloads/'"
             % (PW, names.replace(" ", " /tmp/"), REMOTE_DIR, REMOTE_DIR, REMOTE_DIR))

    print("--- 接口自检 ---")
    rc, out, _ = run(cli, "curl -s --noproxy '*' --max-time 20 "
                          "http://127.0.0.1:8099/api/downloads", timeout=60)
    ok = '"ready":true' in out.replace(" ", "")
    print("api/downloads ready=", ok)

    print("--- 实链下载校验（走容器外网端口）---")
    for _sp, name, size in staging:
        run(cli, "curl -s --noproxy '*' --max-time 180 -o /tmp/_dl_%s -w 'http=%%{http_code} "
                 "bytes=%%{size_download} time=%%{time_total}s\\n' "
                 "http://127.0.0.1:8099/download/%s && sha256sum /tmp/_dl_%s && rm -f /tmp/_dl_%s"
                 % (name, name, name, name), timeout=240)

    cli.close()
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("DEPLOY_PKG", "OK" if ok else "CHECK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
