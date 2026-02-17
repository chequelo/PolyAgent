#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# PolyAgent v2 — Hetzner VPS Setup Script
# Run this on a fresh Ubuntu 24.04 VPS in Amsterdam
#
# Usage: ssh root@YOUR_VPS_IP 'bash -s' < setup_vps.sh
# Or:    scp setup_vps.sh root@YOUR_VPS_IP:~ && ssh root@YOUR_VPS_IP ./setup_vps.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

echo "══════════════════════════════════════"
echo "  PolyAgent v2 — VPS Setup"
echo "══════════════════════════════════════"

# ── 1. System updates ──
echo "📦 Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

# ── 2. Install Docker ──
echo "🐳 Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

# Install Docker Compose plugin
if ! docker compose version &> /dev/null; then
    apt-get install -y -qq docker-compose-plugin
fi

echo "   Docker: $(docker --version)"
echo "   Compose: $(docker compose version)"

# ── 3. Create app user ──
echo "👤 Creating polyagent user..."
if ! id "polyagent" &>/dev/null; then
    useradd -m -s /bin/bash polyagent
    usermod -aG docker polyagent
fi

# ── 4. Setup project directory ──
echo "📁 Setting up project..."
APP_DIR="/home/polyagent/app"
mkdir -p "$APP_DIR"

# ── 5. Install fail2ban + firewall ──
echo "🔒 Configuring security..."
apt-get install -y -qq fail2ban ufw

ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable

systemctl enable fail2ban
systemctl start fail2ban

# ── 6. Setup swap (useful for small VPS) ──
echo "💾 Configuring swap..."
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ── 7. Timezone ──
timedatectl set-timezone UTC

# ── 8. Create .env template ──
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" << 'ENVEOF'
# PolyAgent v2 — Fill in your keys
ANTHROPIC_API_KEY=
TAVILY_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

POLYMARKET_PRIVATE_KEY=
POLYMARKET_FUNDER_ADDRESS=
POLY_BANKROLL=20

HYPERLIQUID_PRIVATE_KEY=
HYPERLIQUID_WALLET_ADDRESS=
HL_BANKROLL=20
ENVEOF
    echo "   ⚠️  Created $APP_DIR/.env — FILL IN YOUR KEYS!"
fi

# ── 9. Create deploy script ──
cat > /home/polyagent/deploy.sh << 'DEPLOYEOF'
#!/bin/bash
# Quick deploy/update script
cd /home/polyagent/app

echo "🔄 Pulling latest code..."
git pull origin main 2>/dev/null || echo "Not a git repo, skipping pull"

echo "🏗️ Building..."
docker compose build

echo "🚀 Starting..."
docker compose up -d

echo "✅ PolyAgent v2 running!"
echo "   Logs: docker compose logs -f"
DEPLOYEOF
chmod +x /home/polyagent/deploy.sh

# ── 10. Create log viewer ──
cat > /home/polyagent/logs.sh << 'LOGSEOF'
#!/bin/bash
cd /home/polyagent/app && docker compose logs -f --tail=50
LOGSEOF
chmod +x /home/polyagent/logs.sh

# ── 11. Set ownership ──
chown -R polyagent:polyagent /home/polyagent

echo ""
echo "══════════════════════════════════════"
echo "  ✅ VPS Setup Complete!"
echo "══════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Upload your code:  scp -r polyagent-v2/* polyagent@YOUR_VPS:~/app/"
echo "  2. Edit .env:         ssh polyagent@YOUR_VPS 'nano ~/app/.env'"
echo "  3. Deploy:            ssh polyagent@YOUR_VPS '~/deploy.sh'"
echo "  4. View logs:         ssh polyagent@YOUR_VPS '~/logs.sh'"
echo ""
echo "Or clone from GitHub:"
echo "  ssh polyagent@YOUR_VPS"
echo "  cd ~/app && git clone https://github.com/chequelo/prediction_markets.git ."
echo "  cp /home/polyagent/app/.env ."
echo "  ~/deploy.sh"
echo ""
