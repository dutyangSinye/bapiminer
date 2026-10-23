#!/bin/bash
set -e
cd /opt/minrigs

echo "=== 1. build ==="
docker build -t minrigs-server:1.0.0 . 2>&1 | tail -8

echo "=== 2. compose file ==="
cat > /opt/minrigs/docker-compose.yml <<'EOF'
# 扒皮矿工中控服务 (minrigs)
# 部署：cd /opt/minrigs && docker compose up -d --build   （本机无 compose 插件时用 deploy.sh）
# 升级：替换 server.py / static/ 后 docker restart minrigs
# 端口：8099（容器内同端口），对外由 nginx 反代 mine.sinyerobot.com
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
    environment:
      - TZ=Asia/Shanghai
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"
EOF

echo "=== 3. run ==="
docker rm -f minrigs >/dev/null 2>&1 || true
docker run -d --name minrigs --restart always \
  -p 8099:8099 \
  -v /opt/minrigs/data:/app/data \
  -v /opt/minrigs/config.json:/app/config.json:ro \
  -e TZ=Asia/Shanghai \
  --log-opt max-size=20m --log-opt max-file=3 \
  minrigs-server:1.0.0 >/dev/null

sleep 4
echo "=== 4. status ==="
docker ps --filter name=minrigs --format '{{.Names}} | {{.Status}} | {{.Ports}}'

echo "=== 5. logs ==="
docker logs --tail 20 minrigs 2>&1

echo "=== 6. probe ==="
for u in / /api/downloads /api/rigs; do
  printf '%-18s -> ' "$u"
  curl -s -o /dev/null -w '%{http_code}\n' --max-time 6 "http://127.0.0.1:8099$u"
done
echo "=== 7. data dir ==="
ls -la /opt/minrigs/data
