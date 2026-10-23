#!/bin/bash
# 探测 80/443/8443 是否被云防火墙拦截：临时监听 -> 由外部回连判断（本机先起监听）
mkdir -p /tmp/portprobe && cd /tmp/portprobe
for p in 443 8443; do
  (python3 -m http.server $p --bind 0.0.0.0 >/tmp/portprobe/$p.log 2>&1 &) 
done
sleep 2
ss -lntp | grep -E ':(80|443|8443)\s' || true
