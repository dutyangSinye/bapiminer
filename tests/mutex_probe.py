# -*- coding: utf-8 -*-
"""Probe the single-instance mutex: does the 2nd process actually get blocked?

Usage:
    python mutex_probe.py hold <seconds>
    python mutex_probe.py try
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client"))
import miner_client as mc  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "try"

if mode == "hold":
    ok = mc.acquire_single_instance()
    print("hold: acquire ->", ok, flush=True)
    time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 20)
    print("hold: done", flush=True)
else:
    ok = mc.acquire_single_instance()
    print("try: acquire ->", ok, "(True = got the lock, False = blocked by another instance)", flush=True)
    sys.exit(0 if ok else 3)
