#!/usr/bin/env bash
# The nightly run: re-pull the last 7 days, overwrite them, rebuild the HTML.
#
#   ./run_daily.sh            # 7 days
#   ./run_daily.sh --full     # rebuild every day since each launch
#
# Needs in the environment:
#   GOOGLE_SERVICE_ACCOUNT_JSON (or GOOGLE_SA_KEY_B64)
#   SHOPIFY_NOVELISKA_DOMAIN / _CLIENT_ID / _CLIENT_SECRET
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${PRODUCT_DASHBOARD_VENV:-$HERE/.venv}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "== creating virtualenv at $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"
fi

echo "== build_dashboard.py $*"
"$VENV/bin/python" "$HERE/build_dashboard.py" "$@"

echo "== export_html.py"
"$VENV/bin/python" "$HERE/export_html.py"
