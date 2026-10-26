# -*- coding: utf-8 -*-
"""Check the production board for rigs running more than one miner process.

Two client instances on one rig = two mining kernels fighting over the same
GPU (hashrate halves) AND two notification-area tray icons.  This is the
server-side fingerprint of the duplicate-instance bug.
"""
import os
import json
import urllib.request

BASE = "http://bapimine.click:8099"
TOKEN = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")


def get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Admin-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


data = get("/api/fleet")
rigs = data.get("rigs") or []
print("rigs =", len(rigs))
print()
for rig in rigs:
    name = rig.get("name")
    miners = rig.get("miners") or []
    alive = [m for m in miners if m.get("alive")]
    gpu = rig.get("gpus") or []
    vers = rig.get("client_version") or rig.get("version") or "?"
    print("%-16s online=%-5s ver=%-10s miners=%d alive=%d gpus=%d" % (
        name, rig.get("online"), vers, len(miners), len(alive), len(gpu)))
    for m in alive:
        print("      alive miner: type=%-12s ths=%-8s acc=%-8s pool=%s" % (
            m.get("type"), m.get("ths"), m.get("accepted"), m.get("pool")))
    for m in miners:
        if not m.get("alive"):
            print("      dead  miner: type=%-12s" % (m.get("type"),))
