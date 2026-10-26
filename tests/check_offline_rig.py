# -*- coding: utf-8 -*-
"""查 dutyangsinye 的告警历史与最近采样，判断掉线性质。"""
import os, paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""),
            timeout=25, look_for_keys=False, allow_agent=False)
script = (
    "import sqlite3, time\n"
    "c = sqlite3.connect('/app/data/fleet.db')\n"
    "print('--- 最近 8 条采样 ---')\n"
    "for r in c.execute('select ts,ths,accepted,rejected from samples where rig=? order by ts desc limit 8', ('dutyangsinye',)):\n"
    "    print('   %s ths=%.1f acc=%s rej=%s' % (time.strftime('%m-%d %H:%M:%S', time.localtime(r[0])), r[1], r[2], r[3]))\n"
    "print('--- 告警历史（最近 12 条）---')\n"
    "for r in c.execute('select ts,level,kind,msg,resolved from alerts where rig=? order by ts desc limit 12', ('dutyangsinye',)):\n"
    "    print('   %s [%s] %s resolved=%s' % (time.strftime('%m-%d %H:%M:%S', time.localtime(r[0])), r[1], (r[3] or '')[:70], r[4]))\n"
    "print('--- rigs 行 ---')\n"
    "for r in c.execute('select name,status,last_seen,client_version,pool_url from rigs where name=?', ('dutyangsinye',)):\n"
    "    print('   ', r[0], r[1], time.strftime('%m-%d %H:%M:%S', time.localtime(r[2] or 0)), r[3], r[4])\n"
)
si, so, se = cli.exec_command("docker exec -i minrigs python3 -", timeout=40)
si.write(script)
si.flush()
si.channel.shutdown_write()
print(so.read().decode())
cli.close()
