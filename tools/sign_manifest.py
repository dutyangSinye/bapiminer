# -*- coding: utf-8 -*-
"""发布签名工具：keygen / pubkey / sign / verify。

矿机端只信任「用内置公钥验签通过的清单」，而不是信任中控返回的任何字段。
所以清单必须由**离线私钥**签发，私钥永远不进仓库、不上中控。

清单结构（signed_manifest.json）::

    {
      "schema": 1,
      "issued_at": 1770000000,
      "client":  {"version": "1.13.0.1", "file": "BaPiMiner.exe",
                  "size": 123, "sha256": "<hex>"},
      "kernels": {"srbminer": {"version": "v2.9.9", "file": "srbminer-v2.9.9.zip",
                               "size": 456, "sha256": "<hex>"}},
      "sig": "<128 hex>"
    }

`sig` 覆盖除它自身以外的全部字段（canonical JSON，键排序、无空格、UTF-8）。

典型发版流程::

    # 0) 首次：生成发布密钥（只做一次，之后私钥离线保管）
    python tools/sign_manifest.py keygen
    python tools/sign_manifest.py pubkey           # 把输出填进 miner_client.py

    # 1) 打包客户端
    pyinstaller --clean --noconfirm client/BaPiMiner.spec

    # 2) 取中控的内核目录（含 zip 的 sha256），在离线/开发机上签发清单
    curl -s -H "X-Admin-Token: $TOK" http://host:8099/api/kernels > catalog.json
    python tools/sign_manifest.py sign \
        --exe client/dist/BaPiMiner.exe --version 1.13.0.1 \
        --catalog catalog.json --out server/data/signed_manifest.json

    # 3) 上传 exe + 清单到中控 data/，docker restart
"""

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ed25519  # noqa: E402

# Windows 控制台默认 GBK，中文/告警符号会炸在 print 上；统一走 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_KEY = os.path.join(ROOT, "keys", "publisher_ed25519.key")
SCHEMA = 1
KERNEL_KEYS = ("srbminer", "bzminer", "wildrig")


# ---------------------------------------------------------------- 清单规范


def canonical(manifest):
    """签名/验签用的规范字节。两端必须完全一致。"""
    payload = {k: v for k, v in manifest.items() if k != "sig"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(client_entry, kernel_entries, notes="", issued_at=None):
    return {
        "schema": SCHEMA,
        "issued_at": int(issued_at if issued_at is not None else time.time()),
        "notes": notes or "",
        "client": client_entry,
        "kernels": kernel_entries or {},
    }


def load_key(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read().strip()
    seed = bytes.fromhex(raw)
    if len(seed) != 32:
        raise SystemExit("私钥文件格式不对：应为 64 位十六进制（32 字节种子）")
    return seed


# ---------------------------------------------------------------- 子命令


def cmd_keygen(args):
    path = os.path.abspath(args.key)
    if os.path.exists(path) and not args.force:
        raise SystemExit("私钥已存在：%s\n（确实要覆盖请加 --force，注意旧私钥签过的清单将永久失效）" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    seed = ed25519.new_seed()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(seed.hex() + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print("已生成发布私钥：%s" % path)
    print("公钥（内置进 miner_client.py 的 PUBLISHER_PUBKEY）：")
    print("  %s" % ed25519.public_key_hex(seed))
    print("\n⚠ 这把私钥是整条升级链路的信任根：离线保管、不要进仓库、不要上中控。")
    print("  丢了 = 以后所有矿机都无法接受新版本（只能手动重装）。")
    return 0


def cmd_pubkey(args):
    seed = load_key(os.path.abspath(args.key))
    print(ed25519.public_key_hex(seed))
    return 0


def _kernel_entries_from_catalog(path, only=None):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    catalog = raw.get("catalog") if isinstance(raw, dict) and "catalog" in raw else raw
    if not isinstance(catalog, dict):
        raise SystemExit("catalog 格式不对：需要 {name: {version, file, sha256, size}}")
    out = {}
    for name, meta in catalog.items():
        if only and name not in only:
            continue
        if not isinstance(meta, dict):
            raise SystemExit("catalog[%s] 不是对象" % name)
        sha = str(meta.get("sha256") or "")
        if len(sha) != 64:
            raise SystemExit(
                "catalog[%s] 没有 sha256 —— 中控需先缓存该内核 zip（新代码会自动记录哈希），"
                "或改用 --kernels-dir 直接对本地 zip 计算。" % name)
        out[name] = {
            "version": str(meta.get("version") or ""),
            "file": str(meta.get("file") or ""),
            "size": int(meta.get("size") or 0),
            "sha256": sha.lower(),
        }
    return out


def _kernel_entries_from_dir(path, only=None):
    out = {}
    for fname in sorted(os.listdir(path)):
        if not fname.lower().endswith(".zip"):
            continue
        name = fname.split("-", 1)[0]
        if name not in KERNEL_KEYS or (only and name not in only):
            continue
        full = os.path.join(path, fname)
        version = fname[len(name) + 1:-4]
        out[name] = {
            "version": version,
            "file": fname,
            "size": os.path.getsize(full),
            "sha256": sha256_file(full),
        }
    return out


def cmd_sign(args):
    seed = load_key(os.path.abspath(args.key))
    exe = os.path.abspath(args.exe)
    if not os.path.isfile(exe):
        raise SystemExit("找不到客户端产物：%s" % exe)
    client_entry = {
        "version": args.version.strip(),
        "file": args.client_file or os.path.basename(exe),
        "size": os.path.getsize(exe),
        "sha256": sha256_file(exe),
    }
    if not client_entry["version"]:
        raise SystemExit("--version 不能为空")

    kernels = {}
    if args.catalog:
        kernels.update(_kernel_entries_from_catalog(args.catalog, args.kernel))
    if args.kernels_dir:
        from_dir = _kernel_entries_from_dir(args.kernels_dir, args.kernel)
        for k, v in from_dir.items():
            kernels.setdefault(k, v)
    if args.kernel:
        missing = [k for k in args.kernel if k not in kernels]
        if missing:
            raise SystemExit("指定的内核没有可签内容：%s" % ", ".join(missing))

    manifest = build_manifest(client_entry, kernels, notes=args.notes,
                              issued_at=int(time.time()) if not args.issued_at else args.issued_at)
    manifest["sig"] = ed25519.sign_hex(canonical(manifest), seed)

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print("已签发：%s" % out)
    print("  公钥    : %s" % ed25519.public_key_hex(seed))
    print("  客户端  : v%s  %d 字节  %s…" % (
        client_entry["version"], client_entry["size"], client_entry["sha256"][:16]))
    for name, meta in sorted(kernels.items()):
        print("  内核 %-9s %-12s %d 字节  %s…" % (
            name, meta["version"] or "-", meta["size"], meta["sha256"][:16]))
    if not kernels:
        print("  内核    : （清单里没有内核条目，矿机端将拒绝安装未签名内核）")
    return 0


def cmd_verify(args):
    # utf-8-sig：Windows 上被记事本/PowerShell 碰过的文件常带 BOM
    with open(os.path.abspath(args.manifest), encoding="utf-8-sig") as f:
        manifest = json.load(f)
    if args.pubkey:
        pk = args.pubkey.strip().lower()
    elif args.key:
        pk = ed25519.public_key_hex(load_key(os.path.abspath(args.key)))
    else:
        raise SystemExit("需要 --pubkey 或 --key 之一")
    ok = ed25519.verify_hex(canonical(manifest), str(manifest.get("sig") or ""), pk)
    print("验签%s（公钥 %s…）" % ("通过" if ok else "失败", pk[:16]))
    if ok:
        c = manifest.get("client") or {}
        print("  客户端 v%s  %s…" % (c.get("version"), str(c.get("sha256"))[:16]))
        print("  内核 %d 个：%s" % (len(manifest.get("kernels") or {}),
                                  ", ".join(sorted(manifest.get("kernels") or {})) or "-"))
    return 0 if ok else 3


# ---------------------------------------------------------------- 入口


def main(argv=None):
    ap = argparse.ArgumentParser(description="矿机端升级清单签发工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("keygen", help="生成发布私钥（只做一次）")
    p.add_argument("--key", default=DEFAULT_KEY)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("pubkey", help="打印私钥对应的公钥")
    p.add_argument("--key", default=DEFAULT_KEY)
    p.set_defaults(func=cmd_pubkey)

    p = sub.add_parser("sign", help="签发清单")
    p.add_argument("--key", default=DEFAULT_KEY)
    p.add_argument("--exe", required=True)
    p.add_argument("--client-file", default="", help="对外文件名，默认取 exe 的 basename")
    p.add_argument("--version", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--catalog", default="", help="中控 /api/kernels 的输出 JSON")
    p.add_argument("--kernels-dir", default="", help="本地内核 zip 目录（如 server/data/kernels）")
    p.add_argument("--kernel", action="append", default=[], help="只签指定内核，可重复")
    p.add_argument("--issued-at", type=int, default=0)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("verify", help="校验清单签名")
    p.add_argument("manifest")
    p.add_argument("--pubkey", default="")
    p.add_argument("--key", default="")
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
