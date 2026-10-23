#!/bin/bash
set -e
mkdir -p /opt/minrigs/nginx/conf.d /opt/minrigs/certs /opt/minrigs/www

echo "=== 1. http-only conf ==="
cat > /opt/minrigs/nginx/conf.d/mine.conf <<'EOF'
# mine.sinyerobot.com -> minrigs:8099  (HTTP 阶段，签发证书用)
server {
    listen 80;
    server_name mine.sinyerobot.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:8099;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        client_max_body_size 300m;
    }
}
EOF

echo "=== 2. run nginx (host network) ==="
docker rm -f minrigs-nginx >/dev/null 2>&1 || true
docker run -d --name minrigs-nginx --restart always --network host \
  -v /opt/minrigs/nginx/conf.d:/etc/nginx/conf.d:ro \
  -v /opt/minrigs/certs:/etc/letsencrypt:ro \
  -v /opt/minrigs/www:/var/www/certbot:ro \
  nginx:1.27-alpine >/dev/null
sleep 3
docker ps --filter name=minrigs-nginx --format '{{.Names}} | {{.Status}}'
docker logs --tail 6 minrigs-nginx 2>&1

echo "=== 3. probe via IP host header ==="
curl -s -o /dev/null -w "IP+Host -> %{http_code}\n" -H "Host: mine.sinyerobot.com" --max-time 8 http://127.0.0.1/
curl -s -o /dev/null -w "acme path -> %{http_code}\n" --max-time 8 http://127.0.0.1/.well-known/acme-challenge/probe
