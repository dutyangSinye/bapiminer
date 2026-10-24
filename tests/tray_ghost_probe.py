# -*- coding: utf-8 -*-
"""Isolated test: does a hard process exit leave a ghost tray icon?

Simulates exactly what the client does on upgrade / quit:
  create a pystray icon, let it settle, then os._exit(0) WITHOUT ever
  letting pystray's mainloop run its NIM_DELETE cleanup.

Each run leaves one notification-area entry behind if the theory holds.
"""
import os
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw

TITLE = "GHOSTPROBE-%d" % os.getpid()


def make_img():
    img = Image.new("RGB", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(255, 90, 0))
    return img


icon = pystray.Icon("ghostprobe", make_img(), TITLE, pystray.Menu(pystray.MenuItem("x", lambda *_: None)))
threading.Thread(target=icon.run, daemon=True).start()
time.sleep(1.5)
# hard exit exactly like the client's upgrade path
os._exit(0)
