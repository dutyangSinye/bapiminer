# -*- coding: utf-8 -*-
"""查看中控内核缓存（catalog 版本 + cached zip）。"""
import os
import json
import urllib.request

req = urllib.request.Request(
    "http://%s:8099/api/kernels" % os.environ.get("MINRIGS_HOST", ""),
    headers={"X-Admin-Token": os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")})
d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
print("catalog:")
for k, v in (d.get("catalog") or {}).items():
    print("  %-12s v%-12s %s" % (k, v.get("version"), v.get("file")))
print("cached:")
for c in d.get("cached") or []:
    print("  %-45s %.1f MB" % (c.get("file"), (c.get("size") or 0) / 1048576.0))
