# -*- coding: utf-8 -*-
"""发布新客户端到中控：校验版本 -> 上传 exe -> 写 client_release.json -> 回读确认。

用法：
    set SSH_HOST=... SSH_USER=root SSH_PASS=...
    python release_client.py --version 1.9.0.0 --notes "本次更新内容"
    python release_client.py --show          # 只看中控当前发布状态

前置：miner_client.py 的 CLIENT_VERSION 与 BaPiMiner.spec 三处版本号必须先改成同值，
      再 pyinstaller --clean --noconfirm BaPiMiner.spec 打包。本脚本会核对，不一致直接拒绝。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

import paramiko

EXE = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\client\dist\BaPiMiner.exe"
REMOTE_EXE = "/opt/minrigs/data/downloads/BaPiMiner.exe"
REMOTE_REL = "/opt/minrigs/data/client_release.json"
PUBLIC_API = "http://mine.sinyerobot.com:8099/api/client_latest"


def exe_file_version(path):
    """读 exe 的 PE 版本资源（Windows）。"""
    import ctypes
    from ctypes import wintypes

    ver = ctypes.WinDLL("version")
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return ""
    ptr = ctypes.c_void_p()
    ln = wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
        return ""
    ffi = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
    ms, ls = ffi[2], ffi[3]
    return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(os.environ["SSH_HOST"], port=int(os.environ.get("SSH_PORT", "22")),
                username=os.environ["SSH_USER"], password=os.environ["SSH_PASS"],
                timeout=25, banner_timeout=30, look_for_keys=False, allow_agent=False)
    return cli


def show():
    with urllib.request.urlopen(PUBLIC_API, timeout=12) as r:
        d = json.loads(r.read().decode())
    print("中控当前发布：")
    print("  版本   :", d.get("version") or "(未设置)")
    print("  大小   :", d.get("size"))
    print("  sha256 :", d.get("sha256"))
    print("  说明   :", d.get("notes") or "")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version")
    ap.add_argument("--notes", default="")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    if a.show or not a.version:
        show()
        return 0

    if not os.path.isfile(EXE):
        raise SystemExit("找不到打包产物：%s" % EXE)
    actual = exe_file_version(EXE)
    if actual != a.version:
        raise SystemExit("版本不符：exe 实际为 %s，命令行给的是 %s。\n"
                         "先把 miner_client.py 的 CLIENT_VERSION 和 BaPiMiner.spec 三处改好再打包。"
                         % (actual, a.version))

    print("打包产物 %s  (%.1f MB)  版本 %s" % (EXE, os.path.getsize(EXE) / 1048576, actual))
    cli = connect()
    sftp = cli.open_sftp()
    sftp.put(EXE, REMOTE_EXE)
    got = sftp.stat(REMOTE_EXE).st_size
    if got != os.path.getsize(EXE):
        raise SystemExit("上传大小不一致：远端 %d / 本地 %d" % (got, os.path.getsize(EXE)))
    rel = {"version": a.version, "notes": a.notes or "", "uploaded": int(time.time())}
    with sftp.open(REMOTE_REL, "w") as f:
        f.write(json.dumps(rel, ensure_ascii=False, indent=1))
    sftp.close()
    cli.close()
    print("已上传并写入发布信息：", rel)

    d = show()
    if d.get("size") != os.path.getsize(EXE):
        raise SystemExit("回读大小不一致，发布可能未生效")
    print("\nOK：矿机端下次检查（启动 40 秒后 / 每 6 小时）会自动升级，"
          "也可在看板选中矿机点「⬆ 升级客户端」立即触发。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
