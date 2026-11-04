#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令把「上游新内核」签发进签名清单（并可顺手部署）。

背景：中控会自动发现并下载上游新版内核，但**签名清单必须用离线私钥签发**，
否则中控被入侵就等于能给全机群下发任意 exe。以前这一步全靠人工记得做，
忘了就会出现「看板显示可更新、点升级却没反应」。

用法：
    # 只看有哪些内核待签发
    python tools/kernel_release.py --server http://bapimine.click:8099 --token adm_xxx --check

    # 签发（默认客户端版本取线上最新）
    python tools/kernel_release.py --server http://bapimine.click:8099 --token adm_xxx \
        --exe dist/BaPiMiner.exe --out data/signed_manifest.json

    # 签发并直接部署到中控（SCP/SFTP，密码从环境变量 RELEASE_SSH_PASSWORD 取）
    python tools/kernel_release.py ... --deploy root@host:/opt/minrigs/data/signed_manifest.json

退出码：0 成功（含无待签发），1 失败。
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request


def api(base, path, token, timeout=30):
    req = urllib.request.Request(base.rstrip("/") + path,
                                 headers={"X-Admin-Token": token, "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser(description="签发内核清单（把上游新版纳入签名清单）")
    ap.add_argument("--server", required=True, help="中控地址，如 http://bapimine.click:8099")
    ap.add_argument("--token", required=True, help="管理员 token")
    ap.add_argument("--exe", help="矿机端 exe（写进清单 client 段）")
    ap.add_argument("--client-version", help="客户端版本；省略则取线上最新")
    ap.add_argument("--out", default="signed_manifest.json", help="输出清单路径")
    ap.add_argument("--catalog-out", help="顺带保存本次 catalog（默认 <out>.catalog.json）")
    ap.add_argument("--deploy", help="user@host:/绝对路径，签完直接 SCP 上去")
    ap.add_argument("--check", action="store_true", help="只列出待签发内核，不签发")
    ap.add_argument("--force", action="store_true", help="即使没有待签发也重新签发")
    args = ap.parse_args()

    k = api(args.server, "/api/kernels", args.token)
    catalog = k.get("catalog") or {}
    cached = {c["file"]: c for c in (k.get("cached") or [])}
    signed = k.get("signed") or {}
    pending = k.get("pending") or {}

    print("上游目录:", {n: (m or {}).get("version") for n, m in catalog.items()})
    print("清单已签:", signed or "(无)")
    if pending:
        for n, p in pending.items():
            mark = "已下载" if p.get("downloaded") else "尚未下载（等中控自动下载完再签发）"
            print("  待签发 %-9s %s → %s  [%s]" % (n, p["signed"], p["latest"], mark))
    else:
        print("  没有待签发的内核")

    if args.check:
        return 0
    if not pending and not args.force:
        print("无需签发。")
        return 0
    if not args.exe:
        print("✗ 签发需要 --exe（清单里 client 段要写客户端信息）", file=sys.stderr)
        return 1

    cat = {}
    for name, meta in catalog.items():
        target = (pending.get(name) or {}).get("latest") or signed.get(name) or (meta or {}).get("version")
        if not target:
            print("✗ %s 没有可用版本" % name, file=sys.stderr)
            return 1
        fn = "%s-%s.zip" % (name, target)
        c = cached.get(fn)
        if not c or not c.get("sha256"):
            print("✗ %s 未缓存或无 sha256（%s）。等中控下载完成后再签发。" % (name, fn), file=sys.stderr)
            return 1
        cat[name] = {"file": fn, "version": target, "size": c["size"], "sha256": c["sha256"]}
        print("  纳入 %-9s %s  %s" % (name, target, c["sha256"][:16]))

    catp = args.catalog_out or (args.out + ".catalog.json")
    with open(catp, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)

    ver = args.client_version
    if not ver:
        try:
            ver = api(args.server, "/api/client_latest", args.token)["version"]
        except Exception as e:  # noqa: BLE001
            print("✗ 取客户端版本失败：%s（可用 --client-version 指定）" % e, file=sys.stderr)
            return 1

    notes = "签发内核：" + "、".join("%s %s" % (n, c["version"]) for n, c in cat.items())
    sign = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sign_manifest.py")
    cmd = [sys.executable, sign, "sign", "--exe", args.exe, "--version", ver,
           "--notes", notes, "--catalog", catp, "--out", args.out]
    print("\n运行:", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip()[-800:])
    if r.returncode != 0 or not os.path.exists(args.out):
        print("✗ 签发失败", file=sys.stderr)
        return 1

    if args.deploy:
        return _deploy(args.deploy, args.out)
    print("\n清单已生成：%s\n部署：把该文件放到中控 /opt/minrigs/data/signed_manifest.json 后重启容器即可" % args.out)
    return 0


def _deploy(target, local):
    """user@host:/abs/path 形式，密码取 RELEASE_SSH_PASSWORD。"""
    import hashlib

    import paramiko

    if ":" not in target or "@" not in target.split(":")[0]:
        print("✗ --deploy 需要 user@host:/绝对路径", file=sys.stderr)
        return 1
    hostpart, remote = target.split(":", 1)
    user, host = hostpart.split("@", 1)
    pw = os.environ.get("RELEASE_SSH_PASSWORD") or ""
    if not pw:
        print("✗ 需要环境变量 RELEASE_SSH_PASSWORD", file=sys.stderr)
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, user, pw, timeout=20)
    s = c.open_sftp()
    s.put(local, remote + ".new")
    want = hashlib.sha256(open(local, "rb").read()).hexdigest()
    got = c.exec_command("sha256sum %s.new" % remote)[1].read().decode()
    if want not in got:
        print("✗ 远端校验失败：%s" % got.strip(), file=sys.stderr)
        return 1
    c.exec_command("chmod 644 %s.new && mv %s.new %s" % (remote, remote, remote))[1].channel.recv_exit_status()
    s.close()
    c.close()
    print("✓ 已部署到 %s" % remote)
    return 0


if __name__ == "__main__":
    sys.exit(main())
