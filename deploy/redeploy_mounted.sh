#!/bin/bash
set -e
cd /opt/minrigs

echo "=== 1. rebuild image (keep COPY as fallback) ==="
docker build -t minrigs-server:1.0.0 . 2>&1 | tail -3

echo "=== 2. recreate container with code mounts ==="
docker rm -f minrigs >/dev/null 2>&1 || true
docker run -d --name minrigs --restart always \
  -p 8099:8099 \
  -v /opt/minrigs/data:/app/data \
  -v /opt/minrigs/config.json:/app/config.json:ro \
  -v /opt/minrigs/server.py:/app/server.py:ro \
  -v /opt/minrigs/static:/app/static:ro \
  -e TZ=Asia/Shanghai \
  --log-opt max-size=20m --log-opt max-file=3 \
  minrigs-server:1.0.0 >/dev/null

sleep 4
docker ps --filter name=minrigs --format '{{.Names}} | {{.Status}}'

echo "=== 3. compose file refresh ==="
cat > /opt/minrigs/docker-compose.yml <<'EOF'
# 扒皮矿工中控服务 (minrigs)
# 升级：替换 /opt/minrigs/server.py 或 static/ 后 docker restart minrigs（代码是挂载进去的）
# 端口：8099（该服务器安全组白名单只有 22/8088/8099 等，80/443 不通）
services:
  minrigs:
    build: .
    image: minrigs-server:1.0.0
    container_name: minrigs
    restart: always
    ports:
      - "8099:8099"
    volumes:
      - ./data:/app/data
      - ./config.json:/app/config.json:ro
      - ./server.py:/app/server.py:ro
      - ./static:/app/static:ro
    environment:
      - TZ=Asia/Shanghai
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"
EOF

echo "=== 4. probe ==="
for u in / /api/downloads /api/client_latest; do
  printf '%-22s -> ' "$u"
  curl -s -o /dev/null -w '%{http_code}\n' --max-time 6 "http://127.0.0.1:8099$u"
done
echo "--- client_latest body ---"
curl -s --max-time 6 http://127.0.0.1:8099/api/client_latest
echo
docker exec minrigs grep -c client_latest /app/server.py
