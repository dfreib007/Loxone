#!/usr/bin/env bash
# Loxone Voice Control — interactive installer.
#
# Run on the Raspberry Pi (or any Debian/Ubuntu host):
#
#     curl -fsSL https://raw.githubusercontent.com/dfreib007/Loxone/claude/loxone-voice-control-WNWBH/scripts/setup-pi.sh | bash
#
# or, if you've already cloned the repo:
#
#     bash scripts/setup-pi.sh
#
# What it does:
#   1. Verifies the host is Linux + Docker-capable
#   2. Installs Docker engine + compose plugin if missing
#   3. Clones the repo into ~/loxone-voice (or updates if present)
#   4. Interactively prompts for the values that belong in .env
#   5. Writes .env with chmod 600
#   6. Creates the runtime data dir
#   7. Pulls images and starts the container
#   8. Tails the first ~30 lines of logs so you can verify the handshake
#
# The script is idempotent — running it twice updates rather than reinstalls.

set -euo pipefail

REPO_URL="${LOXONE_REPO_URL:-https://github.com/dfreib007/Loxone.git}"
REPO_BRANCH="${LOXONE_REPO_BRANCH:-claude/loxone-voice-control-WNWBH}"
INSTALL_DIR="${LOXONE_INSTALL_DIR:-$HOME/loxone-voice}"

# ---- ANSI helpers ---------------------------------------------------------

if [ -t 1 ]; then
    C_HDR='\033[1;34m'
    C_OK='\033[1;32m'
    C_WARN='\033[1;33m'
    C_ERR='\033[1;31m'
    C_DIM='\033[2m'
    C_OFF='\033[0m'
else
    C_HDR='' C_OK='' C_WARN='' C_ERR='' C_DIM='' C_OFF=''
fi

step()  { printf "${C_HDR}==>${C_OFF} %s\n" "$*"; }
ok()    { printf "${C_OK}✓${C_OFF}   %s\n" "$*"; }
warn()  { printf "${C_WARN}!${C_OFF}   %s\n" "$*"; }
fail()  { printf "${C_ERR}✗${C_OFF}   %s\n" "$*" >&2; exit 1; }
hint()  { printf "    ${C_DIM}%s${C_OFF}\n" "$*"; }

# ---- Preflight ------------------------------------------------------------

step "Preflight checks"

[ "$(uname -s)" = "Linux" ] || fail "this script targets Linux (Raspberry Pi OS, Debian, Ubuntu)"
[ "$(id -u)" != "0" ] || fail "do not run as root — the script will sudo where needed"
command -v sudo >/dev/null 2>&1 || fail "sudo is required"
command -v curl >/dev/null 2>&1 || sudo apt-get install -y curl

ARCH=$(uname -m)
case "$ARCH" in
    aarch64|arm64|x86_64) ok "architecture $ARCH is supported" ;;
    *) warn "unusual architecture $ARCH — the Docker image may not work, proceeding anyway" ;;
esac

# ---- Docker ---------------------------------------------------------------

step "Docker engine + compose plugin"

if ! command -v docker >/dev/null 2>&1; then
    hint "installing Docker via the get.docker.com convenience script"
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    warn "added $USER to the docker group; log out and back in once this script finishes"
fi

if ! docker compose version >/dev/null 2>&1; then
    sudo apt-get install -y docker-compose-plugin
fi

ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
ok "Compose $(docker compose version --short 2>/dev/null || echo "unknown")"

sudo systemctl enable docker >/dev/null
ok "Docker enabled at boot"

# ---- Repo -----------------------------------------------------------------

step "Repository at $INSTALL_DIR"

if [ ! -d "$INSTALL_DIR/.git" ]; then
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
    ok "cloned $REPO_URL ($REPO_BRANCH)"
else
    (
        cd "$INSTALL_DIR"
        git fetch origin "$REPO_BRANCH"
        git checkout "$REPO_BRANCH"
        git pull --ff-only origin "$REPO_BRANCH"
    )
    ok "updated existing checkout"
fi

cd "$INSTALL_DIR"
mkdir -p data

# ---- .env -----------------------------------------------------------------

ENV_PATH="$INSTALL_DIR/.env"

step "Configuration (.env)"

if [ -f "$ENV_PATH" ]; then
    warn ".env already exists at $ENV_PATH"
    read -r -p "    keep it as-is? [Y/n] " keep
    case "${keep:-Y}" in
        n|N|no|No) ;;
        *) ok "keeping existing .env"; SKIP_ENV=1 ;;
    esac
fi

prompt() {
    # prompt VAR_NAME "Description" [default]
    local var="$1" desc="$2" default="${3:-}"
    local current
    if [ -n "$default" ]; then
        read -r -p "    $desc [$default]: " current
        current="${current:-$default}"
    else
        read -r -p "    $desc: " current
    fi
    printf -v "$var" "%s" "$current"
}

prompt_secret() {
    # prompt_secret VAR_NAME "Description"
    local var="$1" desc="$2" current
    read -r -s -p "    $desc (hidden): " current
    echo
    printf -v "$var" "%s" "$current"
}

if [ -z "${SKIP_ENV:-}" ]; then
    hint "all values stay on this machine; we never send them anywhere"
    echo

    prompt LOXONE_HOST "Miniserver IP or hostname" "192.168.1.87"
    prompt LOXONE_USE_HTTPS "Use HTTPS (true/false)" "true"
    prompt LOXONE_USER "Loxone app-user username" "voice-app"
    prompt_secret LOXONE_PASSWORD "Loxone app-user password"
    prompt_secret ANTHROPIC_API_KEY "Anthropic API key (sk-ant-...)"
    prompt ANTHROPIC_MODEL "Anthropic model id" "claude-opus-4-7"
    prompt_secret TELEGRAM_BOT_TOKEN "Telegram bot token (from @BotFather)"
    prompt TELEGRAM_ALLOWED_USER_IDS "Allowed Telegram user IDs (comma-separated)"

    # Light validation
    [[ "$ANTHROPIC_API_KEY" == sk-ant-* ]] || warn "Anthropic key doesn't start with 'sk-ant-' — sure that's right?"
    [[ "$TELEGRAM_BOT_TOKEN" == *:* ]] || warn "Telegram token doesn't contain ':' — usually it does"
    [[ -n "$TELEGRAM_ALLOWED_USER_IDS" ]] || warn "no Telegram user ids set — nobody will be able to talk to the bot"

    umask 077
    cat > "$ENV_PATH" <<EOF
# Generated by scripts/setup-pi.sh on $(date -Iseconds)
# Do not commit. Do not share.

# --- Loxone Miniserver ---
LOXONE_HOST=$LOXONE_HOST
LOXONE_USE_HTTPS=$LOXONE_USE_HTTPS
LOXONE_VERIFY_TLS=false
LOXONE_USER=$LOXONE_USER
LOXONE_PASSWORD=$LOXONE_PASSWORD

# --- Anthropic ---
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
ANTHROPIC_MODEL=$ANTHROPIC_MODEL

# --- Telegram ---
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_USER_IDS=$TELEGRAM_ALLOWED_USER_IDS

# --- Runtime ---
LOG_LEVEL=INFO
AUDIT_LOG_PATH=/app/data/audit.jsonl
EOF
    chmod 600 "$ENV_PATH"
    ok "wrote $ENV_PATH (mode 600)"

    # Wipe local variables; reduces the window where they're in process memory.
    unset LOXONE_PASSWORD ANTHROPIC_API_KEY TELEGRAM_BOT_TOKEN
fi

# ---- Build & start --------------------------------------------------------

step "Building and starting the container"

docker compose up --build -d
ok "container started"

# ---- Health check ---------------------------------------------------------

step "Health check"

echo "    waiting up to 30 s for the handshake to complete…"
deadline=$((SECONDS + 30))
healthy=
while [ "$SECONDS" -lt "$deadline" ]; do
    if docker compose logs loxone-voice 2>/dev/null | grep -q "miniserver handshake complete"; then
        healthy=1
        break
    fi
    if docker compose logs loxone-voice 2>/dev/null | grep -Eq "Traceback|Error|getkey2 failed|gettoken failed"; then
        break
    fi
    sleep 1
done

if [ -n "$healthy" ]; then
    ok "Miniserver handshake succeeded"
    ok "Telegram bot is running"
    echo
    echo "    Open your Telegram bot and send /start."
    echo "    Logs:           docker compose logs -f loxone-voice"
    echo "    Audit log:      tail -f $INSTALL_DIR/data/audit.jsonl"
    echo "    Update later:   bash $INSTALL_DIR/scripts/setup-pi.sh"
else
    warn "didn't see a successful handshake within 30 s"
    echo "    Last 40 log lines:"
    docker compose logs --tail 40 loxone-voice || true
    echo
    echo "    Common fixes:"
    echo "    - wrong LOXONE_PASSWORD → edit $ENV_PATH and 'docker compose restart'"
    echo "    - Miniserver unreachable → ping the IP from this host first"
    echo "    - bad Telegram token  → '@BotFather' → /token → edit .env"
fi
