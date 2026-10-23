# -*- coding: utf-8 -*-
"""批量导出品牌资产：PNG 多尺寸 + Windows ICO。"""
import os
import subprocess
import time
from PIL import Image

BRAND = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(BRAND, "export")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BUILD_PY = r"C:\Users\Admin\.workbuddy\binaries\python\envs\minrigs-build2\Scripts\python.exe"

JOBS = [
    # (svg, out_png, px, transparent_bg)
    ("logo-mark.svg", "logo-mark-512.png", 512, True),
    ("logo-mark-simple.svg", "logo-mark-simple-512.png", 512, True),
    ("logo-mark.svg", "logo-mark-1024.png", 1024, True),
    ("logo-wordmark-dark.svg", "logo-wordmark-dark.png", 1440, False),
]

# 六边形外接方框（含描边外沿），在 256 viewBox 中为 (8,8)-(248,248)
CROP_1024 = (32, 32, 992, 992)


def shoot(svg, out, w, h, transparent=True):
    bg = "00000000" if transparent else "FFFFFFFF"
    cmd = [
        EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--default-background-color=" + bg,
        "--force-device-scale-factor=1",
        "--window-size=%d,%d" % (w, h),
        "--screenshot=" + out,
        "file:///" + svg.replace("\\", "/"),
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)


def render_sized(svg_path, px_w, px_h, tmp_path):
    """把 SVG 的 width/height 重写为目标尺寸后落临时文件（viewBox 不变，内容等比放大）。"""
    import re
    txt = open(svg_path, encoding="utf-8").read()
    txt = re.sub(r'(<svg[^>]*?)\swidth="\d+"', r'\1 width="%d"' % px_w, txt, count=1)
    txt = re.sub(r'(<svg[^>]*?)\sheight="\d+"', r'\1 height="%d"' % px_h, txt, count=1)
    open(tmp_path, "w", encoding="utf-8").write(txt)
    return tmp_path


def main():
    os.makedirs(EXPORT, exist_ok=True)
    for svg, out, px, transparent in JOBS:
        src = os.path.join(BRAND, svg)
        dst = os.path.join(EXPORT, out)
        if "wordmark" in out:
            w, h = 720, 200
            crop = (0, 0, 720, 200)
            tmp = src
        else:
            w = h = px
            crop = CROP_1024 if px == 1024 else tuple(int(v * px / 1024) for v in CROP_1024)
            tmp = render_sized(src, px, px, os.path.join(EXPORT, "_tmp.svg"))
        pad = 40
        shoot(tmp, dst, w + pad, h + pad, transparent)
        time.sleep(0.6)
        im = Image.open(dst)
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        im = im.crop(crop)
        im.save(dst)
        print("png:", out, im.size)

    # ICO：小尺寸用简标，大尺寸用主标（手工组装 PNG 帧 ICO，Vista+ 标准）
    import io
    import struct as _s
    simple = Image.open(os.path.join(EXPORT, "logo-mark-simple-512.png")).convert("RGBA")
    full = Image.open(os.path.join(EXPORT, "logo-mark-512.png")).convert("RGBA")
    # 512 渲染按 1024 裁剪框等比缩放：裁剪框在 1024 中为 32..992 → 512 中为 16..496
    simple = simple.crop((16, 16, 496, 496))
    full = full.crop((16, 16, 496, 496))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for w in sizes:
        src = simple if w <= 48 else full
        buf = io.BytesIO()
        src.resize((w, w), Image.LANCZOS).save(buf, format="PNG")
        frames.append((w, buf.getvalue()))
    ico_path = os.path.join(EXPORT, "bapiminer.ico")
    with open(ico_path, "wb") as f:
        f.write(_s.pack("<HHH", 0, 1, len(frames)))
        offset = 6 + 16 * len(frames)
        for w, data in frames:
            f.write(_s.pack("<BBBBHHII", w % 256, w % 256, 0, 0, 1, 32, len(data), offset))
            offset += len(data)
        for _, data in frames:
            f.write(data)
    print("ico:", ico_path, os.path.getsize(ico_path))

    # GUI 侧栏 / README 用（从裁剪后的 1024 源缩放）
    src1024 = Image.open(os.path.join(EXPORT, "logo-mark-1024.png")).convert("RGBA")
    for w, name in [(48, "logo-48.png"), (32, "logo-32.png"), (64, "logo-64.png"), (256, "logo-256.png")]:
        src1024.resize((w, w), Image.LANCZOS).save(os.path.join(EXPORT, name))
        print("png:", name)


if __name__ == "__main__":
    main()
