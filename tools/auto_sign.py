#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内核自动签发（在本机跑，私钥不离开本机）

策略（任何一条不满足就跳过，不签）：
  1. 包必须已经在中控缓存里（有 sha256）
  2. **缓存包的 sha256 必须等于 GitHub Release 里同版本 asset 的 digest**
     —— 这条最关键：即使中控被人换了包，hash 对不上就签不出来，
        所以「中控被攻破」不足以让攻击者把恶意内核签进清单。
  3. 默认只签「同 major.minor 的补丁升级」（3.8.4 → 3.8.6 可以；v100.52 → v100.70 不行）
     跨次版本要人工评估，用 --allow-minor 才放行
  4. 客户端 exe **不参与自动签发**（只有内核会被自动签）

用法：
    python tools/auto_sign.py --server http://bapimine.click:8099 --token adm_xxx --dry-run
    python tools/auto_sign.py --server ... --token adm_xxx            # 真的签
    python tools/auto_sign.py ... --kernel srbminer --kernel wildrig  # 只处理指定内核
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed25519          # noqa: E402
import sign_manifest as sm   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KEY = os.path.join(ROOT, "keys", "publisher_ed25519.key")
LOG = os.path.join(ROOT, "data", "auto_sign.log")

REPOS = {
    "srbminer": "doktor83/SRBMiner-Multi",
    "bzminer": "bzminer/bzminer",
    "wildrig": "andru-kun/wildrig-multi",
}
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def api(base, path, token, payload=None, timeout=90):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"X-Admin-Token": token,
                                          "Content-Type": "application/json"})
    with OP.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def nums(v):
    """'v100.70' -> [100, 70]；'3.8.6' -> [3, 8, 6]"""
    out, cur = [], ""
    for ch in str(v or "").lower().lstrip("v"):
        if ch.isdigit():
            cur += ch
        elif cur:
            out.append(int(cur))
            cur = ""
    if cur:
        out.append(int(cur))
    return out


def same_patch_line(old, new):
    """同 major.minor 才算补丁升级。"""
    a, b = nums(old), nums(new)
    return len(a) >= 2 and len(b) >= 2 and a[0] == b[0] and a[1] == b[1]


def gh_assets(repo):
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/releases/latest" % repo,
        headers={"User-Agent": "bapiminer-autosign/1.0",
                 "Accept": "application/vnd.github+json"})
    with OP.open(req, timeout=40) as r:
        rel = json.loads(r.read().decode("utf-8", "replace"))
    tag = (rel.get("tag_name") or "").strip()
    out = {}
    for a in rel.get("assets") or []:
        out[a.get("name")] = {"size": a.get("size"),
                              "digest": (a.get("digest") or "").lower(),
                              "tag": tag}
    return tag, out


def main():
    ap = argparse.ArgumentParser(description="内核自动签发（本机运行）")
    ap.add_argument("--server", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--kernel", action="append", help="只处理这些内核，可重复")
    ap.add_argument("--allow-minor", action="store_true", help="允许跨次版本自动签")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.key):
        log("✗ 私钥不存在：%s" % args.key)
        return 2

    k = api(args.server, "/api/kernels", args.token)
    signed = k.get("signed") or {}
    pending = k.get("pending") or {}
    cached = {c["file"]: c for c in (k.get("cached") or [])}

    if not pending:
        log("没有待签发内核，结束")
        return 0

    plan, skipped = {}, []
    for name, p in sorted(pending.items()):
        if args.kernel and name not in args.kernel:
            skipped.append("%s: 不在本次范围" % name)
            continue
        latest, old = p.get("latest") or "", p.get("signed") or ""
        fn = "%s-%s.zip" % (name, latest)
        c = cached.get(fn)
        if not c or not c.get("sha256"):
            skipped.append("%s: %s 还没下载到中控" % (name, fn))
            continue
        repo = REPOS.get(name)
        if not repo:
            skipped.append("%s: 不认识的仓库" % name)
            continue
        try:
            tag, assets = gh_assets(repo)
        except Exception as e:  # noqa: BLE001
            skipped.append("%s: 查 GitHub 失败 %s" % (name, e))
            continue
        # 找到对应 asset：优先同名，其次同 tag
        a = assets.get(fn) or next((v for n2, v in assets.items()
                                    if v.get("tag") == tag and n2.lower().endswith(".zip")), None)
        if not a:
            skipped.append("%s: GitHub 上没有 %s" % (name, fn))
            continue
        digest = (a.get("digest") or "").replace("sha256:", "")
        if not digest:
            skipped.append("%s: GitHub 没给 digest，无法校验，保守跳过" % name)
            continue
        if digest != c["sha256"]:
            skipped.append("%s: 哈希不一致（GitHub %s… vs 中控 %s…），拒绝签发"
                           % (name, digest[:12], c["sha256"][:12]))
            continue
        if not args.allow_minor and not same_patch_line(old, latest):
            skipped.append("%s: %s → %s 跨次版本，需人工评估（--allow-minor 才放行）"
                           % (name, old, latest))
            continue
        plan[name] = {"file": fn, "version": latest, "size": c["size"], "sha256": c["sha256"]}
        log("✓ 通过校验 %s %s → %s（GitHub digest 一致）" % (name, old, latest))

    for s in skipped:
        log("· 跳过 %s" % s)
    if not plan:
        log("没有可自动签发的内核")
        return 0

    kernels = {}
    for name, meta in (k.get("catalog") or {}).items():
        if name in plan:
            kernels[name] = plan[name]
            continue
        v = signed.get(name) or (meta or {}).get("version")
        fn = "%s-%s.zip" % (name, v)
        c = cached.get(fn)
        if not c or not c.get("sha256"):
            log("✗ %s 保持 %s 但缓存缺失，中止（避免签出不完整清单）" % (name, v))
            return 1
        kernels[name] = {"file": fn, "version": v, "size": c["size"], "sha256": c["sha256"]}

    cur = api(args.server, "/api/signed_manifest", args.token)
    client = cur.get("client") or {}
    if not client.get("sha256"):
        log("✗ 线上清单缺 client 段")
        return 1

    seed = sm.load_key(os.path.abspath(args.key))
    man = sm.build_manifest(
        client, kernels,
        notes="自动签发：" + "、".join("%s %s" % (n, m["version"]) for n, m in plan.items()))
    man["sig"] = ed25519.sign_hex(sm.canonical(man), seed)
    ok = ed25519.verify_hex(sm.canonical(man), man["sig"], ed25519.public_key_hex(seed))
    if not ok:
        log("✗ 本地验签失败，中止")
        return 3

    if args.dry_run:
        log("[dry-run] 将签发：%s" % {n: m["version"] for n, m in kernels.items()})
        return 0

    res = api(args.server, "/api/signed_manifest", args.token, payload=man, timeout=90)
    if not res.get("ok"):
        log("✗ 上传失败：%s" % res.get("error"))
        return 4
    log("✓ 已签发并上传：%s" % res.get("kernels"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
