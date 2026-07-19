#!/usr/bin/env bash
# =============================================================================
# vps-sync.sh — rsync danych i wynikow miedzy PC (WSL) a VPS research runnerem.
#
# Dwa kierunki (patrz docs/guides/vps-research-runner.md, Decyzja 2 i 4):
#   up    PC  -> VPS : bot_data/processed/  (dane wejsciowe backtestu)
#   down  VPS -> PC  : results/             (wyniki sweepow/WF/backtestow)
#
# Uzycie:
#   VPS_HOST=algo-vps ./scripts/vps-sync.sh up
#   VPS_HOST=algo-vps ./scripts/vps-sync.sh down
#   VPS_HOST=algo-vps ./scripts/vps-sync.sh up --dry-run
#
# Konfiguracja przez zmienne srodowiskowe:
#   VPS_HOST   (WYMAGANE)  alias SSH albo user@host, np. "algo-vps" lub
#                          "ubuntu@57.128.247.79". Zalecany alias w ~/.ssh/config.
#   VPS_REPO   (opc.)      sciezka repo na VPS. Default: ~/quant_projects/algo_bot
#   RSYNC_OPTS (opc.)      dodatkowe flagi rsync (np. --bwlimit=10000).
#
# Uwaga bezpieczenstwa (Decyzja 5): ten skrypt NIE dotyka .env ani kluczy API —
# przesyla wylacznie dane rynkowe (processed) i wyniki (results). Backtesty
# nie wymagaja sekretow, wiec sekrety zostaja na PC.
#
# Swiadomie BEZ --delete: rsync tutaj tylko dodaje/aktualizuje pliki, nigdy
# nie kasuje po drugiej stronie. Czyszczenie robisz recznie, zeby jeden zly
# VPS_HOST nie wyczyscil lokalnych results/.
# =============================================================================
set -euo pipefail

# --- lokalizacja repo (katalog nadrzedny wzgledem scripts/) ------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- konfiguracja ------------------------------------------------------------
VPS_REPO="${VPS_REPO:-~/quant_projects/algo_bot}"
RSYNC_OPTS="${RSYNC_OPTS:-}"

usage() {
    cat >&2 <<EOF
Usage: VPS_HOST=<alias|user@host> $0 <up|down> [--dry-run]

  up      PC  -> VPS : bot_data/processed/  (dane wejsciowe)
  down    VPS -> PC  : results/             (wyniki)

Env:
  VPS_HOST   wymagane (SSH alias lub user@host)
  VPS_REPO   sciezka repo na VPS (default: ~/quant_projects/algo_bot)
  RSYNC_OPTS dodatkowe flagi rsync
EOF
    exit 2
}

[[ $# -ge 1 ]] || usage
DIRECTION="$1"; shift || true

DRY_RUN=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="--dry-run" ;;
        *) echo "Nieznany argument: $arg" >&2; usage ;;
    esac
done

if [[ -z "${VPS_HOST:-}" ]]; then
    echo "BLAD: VPS_HOST nie ustawiony." >&2
    usage
fi

# -a archiwum, -v verbose, -z kompresja, -h human, --partial wznawialne,
# --progress pasek. Bez --delete (patrz naglowek).
BASE_OPTS=(-avzh --partial --progress ${DRY_RUN:+$DRY_RUN} ${RSYNC_OPTS})

run_rsync() {
    local src="$1" dst="$2"
    echo "==> rsync ${DRY_RUN:+[DRY-RUN] }${src}  ->  ${dst}"
    rsync "${BASE_OPTS[@]}" "$src" "$dst"
}

case "$DIRECTION" in
    up)
        # Trailing slash na src: kopiuj ZAWARTOSC processed/, nie sam katalog.
        LOCAL_SRC="${REPO_ROOT}/bot_data/processed/"
        REMOTE_DST="${VPS_HOST}:${VPS_REPO}/bot_data/processed/"
        if [[ ! -d "$LOCAL_SRC" ]]; then
            echo "BLAD: brak lokalnego ${LOCAL_SRC} — najpierw algo-fetch/algo-process." >&2
            exit 1
        fi
        # Zwykly rsync --dry-run nie zmienia plikow, ale ponizsze mkdir byloby
        # osobna, realna mutacja. W trybie podgladu nie wykonuj zadnego polecenia
        # SSH; katalog docelowy musi juz istniec, aby rsync mogl go porownac.
        if [[ -z "$DRY_RUN" ]]; then
            ssh "$VPS_HOST" "mkdir -p ${VPS_REPO}/bot_data/processed"
        else
            echo "==> [DRY-RUN] pomijam zdalne mkdir -p ${VPS_REPO}/bot_data/processed"
        fi
        run_rsync "$LOCAL_SRC" "$REMOTE_DST"
        ;;
    down)
        REMOTE_SRC="${VPS_HOST}:${VPS_REPO}/results/"
        LOCAL_DST="${REPO_ROOT}/results/"
        mkdir -p "$LOCAL_DST"
        run_rsync "$REMOTE_SRC" "$LOCAL_DST"
        ;;
    *)
        echo "Nieznany kierunek: ${DIRECTION}" >&2
        usage
        ;;
esac

echo "==> Gotowe (${DIRECTION})."
