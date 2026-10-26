# -*- coding: utf-8 -*-
"""发布新客户端到中控：校验版本 -> 上传 exe -> 写 client_release.json -> 回读确认。

用法：
    set SSH_HOST=... SSH_USER=root SSH_PASS=...
    python release_client.py --version 1.13.0.1 --notes "本次更新内容" \
        --manifest server/data/signed_manifest.json
    python release_client.py --show          # 只看中控当前发布状态

前置：miner_client.py 的 CLIENT_VERSION 与 BaPiMiner.spec 三处版本号必须先改成同值，
      再 pyinstaller --clean --noconfirm BaPiMiner.spec 打包。本脚本会核对，不一致直接拒绝。

⭐ 1.13.0.1 起矿机端只认**签名清单**：
   先 tools/sign_manifest.py sign 生成清单，再用 --manifest 一并上传。
   上传前脚本会本地验签并核对 exe 指纹，避免「换了 exe 忘了重签」导致全群静默停更。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXE = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\client\dist\BaPiMiner.exe"
REMOTE_EXE = "/opt/minrigs/data/downloads/BaPiMiner.exe"
REMOTE_REL = "/opt/minrigs/data/client_release.json"
REMOTE_MANIFEST = "/opt/minrigs/data/signed_manifest.json"
PUBLIC_API = "http://bapimine.click:8099/api/client_latest"
PUBLIC_HEALTH = "http://bapimine.click:8099/api/health"


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _client_pubkey():
    """从 miner_client.py 读内置公钥：避免发布脚本和客户端各存一份、慢慢不同步。"""
    path = os.path.join(ROOT, "client", "miner_client.py")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'PUBLISHER_PUBKEY = "([0-9a-f]{64})"', f.read())
        return m.group(1) if m else ""
    except OSError:
        return ""


def verify_manifest(path, expect_version, exe_path):
    """上传前先本地验签 + 核对版本和 exe 指纹。

    这一步是防「exe 换了但忘了重新签清单」：那种情况下矿机端会全部拒绝升级
    （fail closed，不会装坏包），但整个机群会静默停在旧版本，很难发现。
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import ed25519
    import sign_manifest

    with open(path, encoding="utf-8-sig") as f:
        doc = json.load(f)
    pubkey = _client_pubkey()
    if not pubkey:
        return False, "读不到 client/miner_client.py 里的 PUBLISHER_PUBKEY"
    if not ed25519.verify_hex(sign_manifest.canonical(doc), str(doc.get("sig") or ""), pubkey):
        return False, "清单验签失败（不是发布私钥签的，或内容被改过）"
    client = doc.get("client") or {}
    if str(client.get("version") or "") != expect_version:
        return False, "清单版本 %s 与本次发布 %s 不一致" % (
            client.get("version") or "(空)", expect_version)
    actual = _sha256_file(exe_path)
    if str(client.get("sha256") or "").lower() != actual:
        return False, "清单里的 sha256 与本地 exe 不一致（改完 exe 要重新签清单）"
    return True, "版本 %s、%d 个内核条目" % (
        expect_version, len(doc.get("kernels") or {}))


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
    ap.add_argument("--manifest", default="",
                    help="已签名清单（tools/sign_manifest.py sign 的产物）；强烈建议一并上传")
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

    # 上传前先在本地把清单验一遍：宁可这次发布失败，也不要让整个机群静默停在旧版
    manifest_path = os.path.abspath(a.manifest) if a.manifest else ""
    if manifest_path:
        if not os.path.isfile(manifest_path):
            raise SystemExit("找不到清单：%s" % manifest_path)
        ok, detail = verify_manifest(manifest_path, a.version, EXE)
        if not ok:
            raise SystemExit("清单不可用：%s" % detail)
        print("清单校验通过：%s" % detail)
    else:
        print("⚠ 未提供 --manifest：矿机端从 1.13.0.1 起只认签名清单，"
              "本次发布后它们不会升级。")

    print("打包产物 %s  (%.1f MB)  版本 %s" % (EXE, os.path.getsize(EXE) / 1048576, actual))
    cli = connect()
    sftp = cli.open_sftp()
    try:
        sftp.put(EXE, REMOTE_EXE)
        got = sftp.stat(REMOTE_EXE).st_size
        if got != os.path.getsize(EXE):
            raise SystemExit("上传大小不一致：远端 %d / 本地 %d" % (got, os.path.getsize(EXE)))
        rel = {"version": a.version, "notes": a.notes or "", "uploaded": int(time.time())}
        with sftp.open(REMOTE_REL, "w") as f:
            f.write(json.dumps(rel, ensure_ascii=False, indent=1))
        if manifest_path:
            # 清单最后传：它是「这次发布生效」的提交点
            sftp.put(manifest_path, REMOTE_MANIFEST)
    finally:
        sftp.close()
        cli.close()
    print("已上传并写入发布信息：", rel)

    d = show()
    if d.get("size") != os.path.getsize(EXE):
        raise SystemExit("回读大小不一致，发布可能未生效")

    if manifest_path:
        try:
            with urllib.request.urlopen(PUBLIC_HEALTH, timeout=12) as r:
                h = json.loads(r.read().decode())
            sm = h.get("signed_manifest") or {}
            if not sm.get("present"):
                raise SystemExit("中控没读到签名清单（/api/health 显示 present=false）")
            if sm.get("client_version") != a.version:
                raise SystemExit("中控上的清单版本是 %r，与本次 %s 不一致"
                                 % (sm.get("client_version"), a.version))
            print("中控已生效：server %s / commit %s / 清单 v%s（内核 %s）" % (
                h.get("server_version") or "?", h.get("build_commit") or "?",
                sm.get("client_version"), ", ".join(sm.get("kernels") or []) or "-"))
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print("⚠ 上传已完成，但健康检查没通过：%s" % e)

    print("\nOK：矿机端下次检查（启动 40 秒后 / 每 6 小时）会验签后自动升级，"
          "也可在看板选中矿机点「⬆ 升级客户端」立即触发。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
