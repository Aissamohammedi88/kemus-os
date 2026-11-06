#!/usr/bin/env bash
# =============================================================================
#  Kemus 1.0 — Installateur + Lanceur 1-commande
#
#  Usage :
#     bash install.sh                # installe PUIS lance Kemus (port 3922)
#     bash install.sh --user         # install dans ~/.local puis lance
#     bash install.sh --dev          # install editable (-e) puis lance
#     bash install.sh --no-launch    # installe sans lancer
#     bash install.sh --port 4000    # lance sur un port custom
#     bash install.sh --no-browser   # ne pas ouvrir le navigateur
#
#  Apres installation :
#     kemus                          # relance le PC virtuel plus tard
#     http://localhost:3922
# =============================================================================
set -uo pipefail
# Note : pas de 'set -e' global — on gere les erreurs explicitement
# pour pouvoir faire un fallback pip (system -> user).

MODE="auto"
LAUNCH=1
OPEN_BROWSER=1
PORT="${KEMUS_PORT:-3922}"

while [ $# -gt 0 ]; do
  case "$1" in
    --user)      MODE="user" ;;
    --dev)       MODE="dev" ;;
    --no-launch) LAUNCH=0 ;;
    --no-browser) OPEN_BROWSER=0 ;;
    --port)      shift; PORT="${1:-3922}" ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^#//; s/^ //'
      exit 0 ;;
    *)
      echo "Option inconnue : $1"
      echo "Usage : bash install.sh [--user|--dev] [--port N] [--no-launch] [--no-browser]"
      exit 1 ;;
  esac
  shift
done

PY="${PYTHON:-python3}"

echo "============================================================"
echo "  Kemus 1.0 - Installateur + Lanceur 1-commande"
echo "============================================================"

# --- 0. bash requis (on utilise BASH_SOURCE) --------------------------------
if [ -z "${BASH_VERSION:-}" ]; then
  echo "ERREUR : lancez avec bash :  bash install.sh"
  exit 1
fi

# --- 1. Python >= 3.8 (verif propre, sans traceback) ------------------------
PYVER="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
if [ -z "$PYVER" ]; then
  echo "ERREUR : python3 introuvable. Installez Python 3.8+ puis relancez."
  exit 1
fi
PYOK="$($PY -c 'import sys; print(1 if sys.version_info >= (3,8) else 0)' 2>/dev/null)"
if [ "$PYOK" != "1" ]; then
  echo "ERREUR : Python 3.8+ requis (trouve : $PYVER)."
  exit 1
fi
echo "  Python  : $PYVER  ($($PY -c 'import sys; print(sys.executable)'))"

# --- 2. Se placer dans le dossier du script ---------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
if [ ! -f kemus.py ]; then
  echo "ERREUR : kemus.py introuvable dans $SCRIPT_DIR"
  exit 1
fi
echo "  Dossier : $SCRIPT_DIR"

# --- 3. Detection venv (dans un venv, --user est interdit) ------------------
IN_VENV="$($PY -c 'import sys; print(1 if (sys.prefix != getattr(sys, "base_prefix", sys.prefix)) or hasattr(sys, "real_prefix") else 0)' 2>/dev/null || echo 0)"
if [ "$IN_VENV" = "1" ]; then
  echo "  Environnement : venv detecte -> installation locale au venv"
  [ "$MODE" = "auto" ] && MODE="venv"
fi

# --- 4. Installation pip avec chaine de fallback ----------------------------
do_install() {
  $PY -m pip install "$@" . --quiet --disable-pip-version-check --no-input
}

install_ok=0
case "$MODE" in
  venv|dev)
    # dev = editable ; dans un venv on installe directement
    if [ "$MODE" = "dev" ]; then
      echo ">>> Installation editable (pip install -e .)..."
      do_install -e && install_ok=1
    else
      echo ">>> Installation (pip install .)..."
      do_install && install_ok=1
    fi
    ;;
  user)
    echo ">>> Installation utilisateur (pip install --user .)..."
    do_install --user && install_ok=1
    ;;
  auto|*)
    if [ "$IN_VENV" = "1" ]; then
      echo ">>> Installation (pip install .)..."
      do_install && install_ok=1
    else
      echo ">>> Installation systeme (pip install .)..."
      if do_install; then
        install_ok=1
      else
        echo ">>> Installation systeme impossible -> fallback --user..."
        do_install --user && install_ok=1
      fi
    fi
    ;;
esac

if [ "$install_ok" != "1" ]; then
  echo ""
  echo "ERREUR : pip install a echoue (systeme ET user)."
  echo "Solution zero-install : lancez directement  python3 kemus.py"
  exit 1
fi

# --- 5. Verification de l'import --------------------------------------------
echo ">>> Verification de l'import..."
if ! $PY -c "import kemus; print('  OK : Kemus', kemus.VERSION, '- port par defaut', kemus.PORT)" 2>/dev/null; then
  echo "ERREUR : import kemus echoue apres installation."
  echo "Solution zero-install : lancez directement  python3 kemus.py"
  exit 1
fi

# --- 6. Localiser le meilleur lanceur ---------------------------------------
BIN="$(command -v kemus 2>/dev/null || true)"
if [ -n "$BIN" ]; then
  LAUNCHER="$BIN"
  LAUNCH_DESC="$BIN"
elif $PY -c "import kemus" 2>/dev/null; then
  LAUNCHER="$PY -m kemus"
  LAUNCH_DESC="$PY -m kemus"
else
  LAUNCHER="$PY $SCRIPT_DIR/kemus.py"
  LAUNCH_DESC="$PY $SCRIPT_DIR/kemus.py   (zero-install)"
fi

echo ""
echo "============================================================"
echo "  Installation reussie"
echo ""
echo "  Lanceur       : $LAUNCH_DESC"
echo "  Port          : $PORT"
echo "  Fichiers 'PC' : ~/.kemus/home/"
echo "  Logs          : ~/.kemus/kemus.log"
echo ""
echo "  Prochaines fois (sans reinstall) :"
[ -n "$BIN" ] && echo "     kemus"
echo "     $LAUNCHER"
echo "============================================================"

# --- 7. Lancement ------------------------------------------------------------
if [ "$LAUNCH" != "1" ]; then
  echo "(--no-launch : Kemus n'est pas lance. Lancez : $LAUNCHER)"
  exit 0
fi

# Ouvrir le navigateur apres un court delai (le temps que le serveur monte)
if [ "$OPEN_BROWSER" = "1" ]; then
  (
    sleep 1.2
    for b in xdg-open open sensible-browser firefox google-chrome chromium; do
      command -v "$b" >/dev/null 2>&1 || continue
      "$b" "http://127.0.0.1:$PORT/" >/dev/null 2>&1 &
      break
    done
  ) 2>/dev/null &
fi

export KEMUS_PORT="$PORT"
echo ""
echo ">>> Demarrage de Kemus sur http://127.0.0.1:$PORT/  (Ctrl+C pour arreter)"
echo ""

cleanup() {
  echo ""
  echo "[Kemus] Serveur arrete. A bientot !"
  exit 0
}
trap cleanup INT TERM

# exec remplace le shell par le serveur : Ctrl+C va directement a Kemus
exec $LAUNCHER
