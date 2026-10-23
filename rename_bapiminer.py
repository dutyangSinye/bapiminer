# -*- coding: utf-8 -*-
"""扒皮矿工（BaPiMiner）全链路改名：MinRigs -> 扒皮矿工 / BaPiMiner。"""
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# 长串优先
PAIRS = [
    ("MinRigs 群控", "扒皮矿工"),
    ("MinRigs群控", "扒皮矿工"),
    ("MinRigs 矿机端已就绪", "扒皮矿工已就绪"),
    ("MinRigs 矿机端", "扒皮矿工·矿机端"),
    ("MinRigs 挖矿群控", "扒皮矿工挖矿群控"),
    ("MinRigs-android.apk", "BaPiMiner-android.apk"),
    ("MinRigs.exe", "BaPiMiner.exe"),
    ("MinRigs/1.0", "BaPiMiner/1.0"),
    ("MinRigsMinerClient", "BaPiMinerClient"),
    ("退出 MinRigs？", "退出扒皮矿工？"),
    ("MinRigs 已在运行", "扒皮矿工已在运行"),
    ("MinRigs 正在退出", "扒皮矿工正在退出"),
    ("MinRigs v1.4", "扒皮矿工 v1.4"),
    ("MinRigs v1.3", "扒皮矿工 v1.3"),
    ("MinRigs selftest", "BaPiMiner selftest"),
    ("MinRigs", "BaPiMiner"),  # 英文语境兜底
    ("minrigs-client", "bapiminer-client"),
    ("minrigs-server/1.0", "bapiminer-server/1.0"),
    ("minrigs/1.0", "bapiminer/1.0"),
    (".minrigs_kernel.json", ".bapiminer_kernel.json"),
    ("minrigs_admin_token", "bapiminer_admin_token"),
    ("minrigs.exe", "bapiminer.exe"),
]

FILES = [
    "client/miner_client.py",
    "server/server.py",
    "server/static/index.html",
    "android-uniapp/MinRigsUni/manifest.json",
    "android-uniapp/MinRigsUni/pages.json",
    "android-uniapp/MinRigsUni/pages/index/index.vue",
    "android-uniapp/MinRigsUni/index.html",
    "android-uniapp/MinRigsUni/App.vue",
    "tests/e2e_tray_close.py",
    "tests/e2e_kernel_update.py",
    "tests/e2e_downloads.py",
    "tests/e2e_server_kernel.py",
    "tests/e2e_gamemode.py",
    "tests/shot_minrigs.js",
    "tests/shot_full.js",
    "tests/seed_demo.py",
    "deploy/deploy_client_pkg.py",
    "agent/agent.py",
]


def main():
    for rel in FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            print("SKIP(缺):", rel)
            continue
        with io.open(p, encoding="utf-8") as f:
            txt = f.read()
        orig = txt
        hits = {}
        for old, new in PAIRS:
            c = txt.count(old)
            if c:
                txt = txt.replace(old, new)
                hits[old] = c
        if txt != orig:
            with io.open(p, "w", encoding="utf-8", newline="") as f:
                f.write(txt)
            print("OK", rel, "->", sum(hits.values()), "处", sorted(hits.items(), key=lambda x: -x[1])[:4])
        else:
            print("无变化", rel)

    # spec 复制改名
    spec_src = os.path.join(ROOT, "client", "MinRigs.spec")
    spec_dst = os.path.join(ROOT, "client", "BaPiMiner.spec")
    if os.path.isfile(spec_src):
        with io.open(spec_src, encoding="utf-8") as f:
            t = f.read()
        for old, new in PAIRS:
            t = t.replace(old, new)
        with io.open(spec_dst, "w", encoding="utf-8", newline="") as f:
            f.write(t)
        print("OK 生成", spec_dst)


if __name__ == "__main__":
    main()
