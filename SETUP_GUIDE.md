# 🚀 PolyAgent v2 — Setup Guide: Claude Code → GitHub → Hetzner VPS

## Paso 1: Preparar el repo local con Claude Code

```bash
# 1a. Clonar tu repo existente
cd ~/projects  # o donde trabajes
git clone https://github.com/chequelo/prediction_markets.git
cd prediction_markets

# 1b. Crear branch para v2 (opcional, o directo a main)
git checkout -b v2-upgrade

# 1c. Copiar los archivos de PolyAgent v2 al repo
# (Los archivos que descargaste del .zip de Claude.ai)
# Estructura final del repo:
#
# prediction_markets/
# ├── CLAUDE.md
# ├── Dockerfile
# ├── docker-compose.yml
# ├── requirements.txt
# ├── .env.example
# ├── .gitignore
# ├── setup_vps.sh
# ├── main.py
# ├── config.py
# ├── notifier.py
# ├── polymarket/
# │   ├── __init__.py
# │   ├── scanner.py
# │   ├── research.py
# │   ├── estimator.py
# │   └── trader.py
# ├── crypto/
# │   ├── __init__.py
# │   ├── funding.py
# │   ├── spreads.py
# │   └── executor.py
# └── .github/
#     └── workflows/
#         └── deploy.yml

# 1d. Abrir Claude Code en el repo
claude
```

Dentro de Claude Code podés decirle:
> "Lee CLAUDE.md para entender el proyecto. Quiero que me ayudes a iterar sobre el código y deployarlo."


## Paso 2: Crear la VPS en Hetzner

```
1. Ir a https://console.hetzner.cloud
2. Crear cuenta (tarjeta de crédito)
3. Crear proyecto "PolyAgent"
4. Add Server:
   - Location: Falkenstein (DE) — el más barato con CX, o Helsinki (FI)
     (Amsterdam solo tiene CPX que cuesta €4.85/mo)
   - Image: Ubuntu 24.04
   - Type: Shared vCPU → CX23 (2 vCPU, 4GB RAM, 40GB NVMe) = €3.49/mo
   - Networking: Public IPv4 ✅
   - SSH Key: Agregar tu SSH key pública
     (si no tenés: ssh-keygen -t ed25519 -C "polyagent")
   - Name: "polyagent"
   - Create & Buy
5. Copiar la IP del servidor (ej: 168.119.xxx.xxx)
```


## Paso 3: Setup inicial de la VPS

```bash
# 3a. Copiar y ejecutar el setup script
scp setup_vps.sh root@TU_VPS_IP:~
ssh root@TU_VPS_IP 'chmod +x setup_vps.sh && ./setup_vps.sh'

# 3b. Agregar tu SSH key al usuario polyagent (para GitHub Actions)
# Generar un par de keys dedicado para deploy:
ssh-keygen -t ed25519 -f ~/.ssh/polyagent_deploy -N ""

# Copiar la key pública al VPS:
ssh-copy-id -i ~/.ssh/polyagent_deploy.pub polyagent@TU_VPS_IP

# Probar conexión:
ssh -i ~/.ssh/polyagent_deploy polyagent@TU_VPS_IP "echo ok"
```


## Paso 4: Configurar GitHub Secrets (para auto-deploy)

```
1. Ir a https://github.com/chequelo/prediction_markets/settings/secrets/actions
2. Agregar estos secrets:

   VPS_HOST      →  168.119.xxx.xxx  (tu IP de Hetzner)
   VPS_SSH_KEY   →  (pegar contenido de ~/.ssh/polyagent_deploy)
                     cat ~/.ssh/polyagent_deploy | pbcopy
```


## Paso 5: Setup inicial del repo en la VPS

```bash
# 5a. Conectarte como polyagent
ssh polyagent@TU_VPS_IP

# 5b. Clonar el repo
cd ~/app
git clone https://github.com/chequelo/prediction_markets.git .

# 5c. Crear el .env con tus keys
nano .env
# (copiar de .env.example y llenar todos los valores)

# 5d. Primer deploy manual
~/deploy.sh

# 5e. Ver logs
~/logs.sh
```


## Paso 6: Push desde Claude Code → Deploy automático

```bash
# En tu máquina local, dentro del repo:
git add .
git commit -m "PolyAgent v2: multi-strategy AI trading agent"
git push origin main  # o: git push origin v2-upgrade && crear PR

# GitHub Actions automáticamente:
# 1. Se conecta a la VPS por SSH
# 2. Hace git pull
# 3. Rebuild del container Docker
# 4. Restart del servicio
```


## Paso 7: Verificar que todo funciona

```bash
# En Telegram, mandar al bot:
/status   # → Debe mostrar todas las keys en ✅
/scan     # → Debe correr el scan completo
/crypto   # → Debe correr scan de crypto
```


## Workflow diario con Claude Code

```bash
# Abrir Claude Code en el repo
cd ~/projects/prediction_markets
claude

# Ejemplos de lo que podés pedirle:
# "Agregá un nuevo par de trading SOL/USDT a las spreads"
# "Mejoré el estimator para que use chain-of-thought más largo"
# "Agregá logging de P&L a un archivo JSON"
# "Hacé que el arb scanner también revise mercados multi-outcome"
# "Corregí el bug en el funding rate calculation"

# Cuando terminás, Claude Code commitea y pushea:
# → GitHub Actions deploya automáticamente a la VPS
```


## Troubleshooting

### Cloudflare bloquea la VPS
```bash
# Probar desde la VPS:
ssh polyagent@TU_VPS_IP
curl -s https://gamma-api.polymarket.com/markets?limit=1 | head -100
# Si da 403 → la IP está bloqueada
# Solución: Destruir server y crear uno nuevo (nueva IP)
```

### El bot no responde en Telegram
```bash
ssh polyagent@TU_VPS_IP
cd ~/app && docker compose logs --tail=20
# Verificar que TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID están correctos
```

### Deploy falla en GitHub Actions
```
1. Ir a https://github.com/chequelo/prediction_markets/actions
2. Click en el run fallido
3. Verificar que VPS_HOST y VPS_SSH_KEY están correctos en secrets
```
