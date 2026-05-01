#!/usr/bin/env bash
# aut-03-run.sh — wrapper Vaultwarden / Bitwarden para telegram-audio-dl
#
# Lee credenciales desde un item de Vaultwarden/Bitwarden (vía `bw` CLI),
# las exporta como env vars y delega al CLI Python. Variables no-secret
# (DOWNLOAD_ROOT, LOG_LEVEL, TELEGRAM_SESSION) siguen viniendo del .env local.
#
# Configuración por env vars antes de invocar:
#   AUT03_ITEM_NAME   — nombre del item en Vaultwarden (default abajo)
#   AUT03_ROOT        — ruta absoluta del proyecto (default /opt/aut-03)
#
# El item debe tener tres custom fields hidden:
#   api_id, api_hash, phone
#
# Uso:
#   bin/aut-03-run.sh                          # CLI interactivo
#   LOG_LEVEL=DEBUG bin/aut-03-run.sh          # con override
#   AUT03_ITEM_NAME="Personal/TG" bin/aut-03-run.sh

set -euo pipefail

readonly ITEM_NAME="${AUT03_ITEM_NAME:-Telegram (AUT-03)}"
readonly PROJECT_ROOT="${AUT03_ROOT:-/opt/aut-03}"

log()  { printf '\033[36m→\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*" >&2; }

# 1. Dependencias
for bin in bw jq; do
  command -v "$bin" >/dev/null 2>&1 || { err "$bin no instalado"; exit 1; }
done

# 2. Estado del vault
status="$(bw status | jq -r '.status')"
case "$status" in
  unauthenticated)
    err "No has hecho 'bw login' todavía"
    err "  bw login --apikey  (recomendado)  o  bw login <email>"
    exit 1
    ;;
  locked)
    log "Vault locked — desbloqueando"
    BW_SESSION="$(bw unlock --raw)"
    [ -n "$BW_SESSION" ] || { err "Unlock falló"; exit 1; }
    export BW_SESSION
    ;;
  unlocked)
    : # ya desbloqueado, BW_SESSION ya está en el entorno
    ;;
  *)
    err "Estado desconocido del vault: $status"
    exit 1
    ;;
esac

# 3. Sync para asegurar últimas creds
bw sync >/dev/null

# 4. Obtener item completo (campos custom no se exponen con `bw get password`)
item_json="$(bw get item "$ITEM_NAME" 2>/dev/null)" || {
  err "Item '$ITEM_NAME' no encontrado en Vaultwarden/Bitwarden"
  err "  Crea el item con campos custom: api_id, api_hash, phone"
  err "  Ajusta el nombre con AUT03_ITEM_NAME si es distinto"
  exit 1
}

# 5. Extraer campos custom
get_field() {
  jq -r --arg name "$1" '.fields[]? | select(.name == $name) | .value' <<<"$item_json" | head -n 1
}

api_id="$(get_field api_id)"
api_hash="$(get_field api_hash)"
phone="$(get_field phone)"

if [ -z "$api_id" ] || [ -z "$api_hash" ] || [ -z "$phone" ]; then
  err "Faltan campos en '$ITEM_NAME'. Requeridos: api_id, api_hash, phone"
  exit 1
fi

export TELEGRAM_API_ID="$api_id"
export TELEGRAM_API_HASH="$api_hash"
export TELEGRAM_PHONE="$phone"

# 6. Cargar variables no-secret desde .env si existe
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_ROOT/.env"
  set +a
fi

# 7. Defaults razonables (ajustables vía .env)
export TELEGRAM_SESSION="${TELEGRAM_SESSION:-telegram_audio_dl}"
export DOWNLOAD_ROOT="${DOWNLOAD_ROOT:-$HOME/Downloads}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# 8. Sanity checks
[ -d "$PROJECT_ROOT" ]                 || { err "No existe $PROJECT_ROOT";              exit 1; }
[ -x "$PROJECT_ROOT/venv/bin/python" ] || { err "No existe venv en $PROJECT_ROOT/venv"; exit 1; }
[ -d "$DOWNLOAD_ROOT" ]                || { err "DOWNLOAD_ROOT no existe: $DOWNLOAD_ROOT"; exit 1; }

ok "Credenciales cargadas desde Vaultwarden — arrancando CLI"

# 9. Invocar CLI con args restantes
cd "$PROJECT_ROOT"
exec ./venv/bin/python -m telegram_audio_dl "$@"
