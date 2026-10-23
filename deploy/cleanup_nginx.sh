#!/bin/bash
# 清理：80/443 不在白名单，nginx 反代无意义；撤掉临时端口探测进程
echo "=== 1. remove nginx ==="
docker rm -f minrigs-nginx >/dev/null 2>&1 || true
rm -rf /opt/minrigs/nginx /opt/minrigs/www
echo "removed"

echo "=== 2. kill temp probes (443/8443 python http.server) ==="
pkill -f "http.server 443" 2>/dev/null || true
pkill -f "http.server 8443" 2>/dev/null || true
rm -rf /tmp/portprobe
ss -lntp | grep -E ':(80|443|8443)\s' || echo "80/443/8443 free"

echo "=== 3. containers ==="
docker ps --format '{{.Names}} | {{.Status}} | {{.Ports}}' | grep -i minrigs

echo "=== 4. domain:8099 probe from server ==="
curl -s -o /dev/null -w "localhost:8099 -> %{http_code}\n" --max-time 6 http://127.0.0.1:8099/
