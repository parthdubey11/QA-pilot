#!/usr/bin/env bash
# One-time (and re-runnable) setup of QA Pilot on a fresh Ubuntu server, e.g. Oracle Cloud Always Free.
# Run from the project folder on the server:   bash deploy/setup.sh
# It installs Docker, opens ports 80/443, writes .env (keeping existing values), and starts the stack.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

# ---------- Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

# ---------- firewall (Oracle's Ubuntu images block everything but SSH in iptables) ----------
if sudo iptables -S INPUT 2>/dev/null | grep -q "REJECT"; then
  for port in 80 443; do
    if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
      say "Opening port $port"
      sudo iptables -I INPUT 5 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
    fi
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then sudo netfilter-persistent save; fi
fi

# ---------- swap (helps on small VMs; harmless on big ones) ----------
if [ "$(free -m | awk '/Mem:/ {print $2}')" -lt 8000 ] && ! swapon --show | grep -q .; then
  say "Adding a 2 GB swap file"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ---------- .env ----------
touch .env
chmod 600 .env
get() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }
setdefault() { [ -n "$(get "$1")" ] || echo "$1=$2" >> .env; }
rand() { python3 -c "import secrets; print(secrets.token_urlsafe($1))"; }

IP="$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)"
DASHED_IP="${IP//./-}"
setdefault QAPILOT_DOMAIN "qapilot-${DASHED_IP}.sslip.io"
setdefault SHOP_DOMAIN "shop-${DASHED_IP}.sslip.io"
setdefault JWT_SECRET "$(rand 48)"
setdefault CREDENTIALS_KEY "$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
setdefault DEMO_SHOP_RESET_TOKEN "$(rand 24)"
setdefault MONGO_DB qapilot
setdefault LLM_PROVIDER groq
if [ -n "$(get GEMINI_API_KEY)" ]; then setdefault LLM_FALLBACKS gemini; fi
setdefault LLM_MIN_INTERVAL_SECONDS 15
setdefault LLM_MAX_RETRIES 5
# Public-server limits: the LLM key's free quota is shared by everyone who signs up.
setdefault MAX_AGENT_RUNS_PER_USER_PER_DAY 2
setdefault MAX_AGENT_RUNS_PER_DAY 6
setdefault MAX_TESTS_LIMIT 6
setdefault BLOCKED_TARGET_HOSTS "mongo,api,worker,frontend,caddy,metadata.google.internal,metadata"

if [ -z "$(get GROQ_API_KEY)" ] && [ -z "$(get GEMINI_API_KEY)" ]; then
  echo
  echo "No LLM key in .env yet. Add GROQ_API_KEY=... (or GEMINI_API_KEY with LLM_PROVIDER=gemini) to"
  echo "$(pwd)/.env and run this script again. Everything else is ready."
  exit 1
fi

# ---------- start ----------
say "Building and starting QA Pilot (the first build takes a while: the browser image is ~2 GB)"
$DOCKER compose -f docker-compose.prod.yml --env-file .env up -d --build --remove-orphans

say "Waiting for the API"
for _ in $(seq 1 60); do
  if $DOCKER compose -f docker-compose.prod.yml exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" 2>/dev/null; then break; fi
  sleep 3
done

echo
echo "QA Pilot:  https://$(get QAPILOT_DOMAIN)"
echo "Demo shop: https://$(get SHOP_DOMAIN)   (use this URL as a project's base URL)"
echo "HTTPS certificates are issued on the first visit; give it a minute if the browser warns."
