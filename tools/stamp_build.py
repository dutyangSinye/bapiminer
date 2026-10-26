# -*- coding: utf-8 -*-
"""写入 server/data/build_info.json —— 让 /api/health 能回答「线上跑的是哪份代码」。

没有这个文件时，中控只能说「我是 3.1.0」，说不出是哪次提交、什么时候构建的；
出了问题只能靠人回忆。有了它，任意时刻 curl 一下 /api/health 就能对上 git。

用法：
    python tools/stamp_build.py                 # 写 server/data/build_info.json
    python tools/stamp_build.py --check         # 只打印当前会写入什么

部署时把 server/data/build_info.json 跟 server.py 一起传上去即可
（docker 挂载的是 data/ 目录，不需要改镜像）。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _git(*args):
    try:
        out = subprocess.run(("git", "-C", ROOT) + args, capture_output=True,
                             text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _server_version(server_dir):
    path = os.path.join(server_dir, "server.py")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(r'^SERVER_VERSION = "([^"]+)"', text, re.M)
    return m.group(1) if m else ""


def build_info(server_dir):
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    describe = _git("describe", "--tags", "--always", "--dirty")
    return {
        "commit": commit[:12] or "unknown",
        "commit_full": commit,
        "describe": describe,
        "dirty": dirty,
        "version": _server_version(server_dir),
        "built_at": int(time.time()),
        "built_by": os.environ.get("USERNAME") or os.environ.get("USER") or "",
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-dir", default=os.path.join(ROOT, "server"))
    ap.add_argument("--out", default="", help="默认 <server-dir>/data/build_info.json")
    ap.add_argument("--check", action="store_true", help="只打印，不写文件")
    args = ap.parse_args(argv)

    info = build_info(args.server_dir)
    out = args.out or os.path.join(args.server_dir, "data", "build_info.json")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    if args.check:
        return 0
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("已写入 %s" % out)
    if info["dirty"]:
        print("⚠ 工作区有未提交改动：dirty=true 会让「线上是哪份代码」存疑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
