# -*- coding: utf-8 -*-
"""探测 SFTP 可写位置"""
import os
import paramiko

t = paramiko.Transport((os.environ["SSH_HOST"], int(os.environ.get("SSH_PORT", "22"))))
t.connect(username=os.environ["SSH_USER"], password=os.environ["SSH_PASS"])
s = paramiko.SFTPClient.from_transport(t)

print("cwd:", s.normalize("."))
try:
    print("home entries:", s.listdir(".")[:20])
except Exception as e:
    print("listdir home fail:", e)

for probe in ["/tmp", "/data_7HKERMAF/minrigs", "/tmp/zfsv2"]:
    try:
        print(probe, "->", s.listdir(probe)[:5])
    except Exception as e:
        print(probe, "-> fail:", e)

# 尝试写 home
try:
    with s.open("minrigs_write_test.txt", "w") as f:
        f.write("ok")
    print("home writable: YES")
    s.remove("minrigs_write_test.txt")
except Exception as e:
    print("home writable: NO", e)
s.close(); t.close()
