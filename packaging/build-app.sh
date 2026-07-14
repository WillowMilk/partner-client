#!/usr/bin/env bash
# Build "Partner Client.app" — the desk with a face.
#
# Prereqs: pip install pyinstaller ; npm run build in partner_client_gui/
# (the Svelte dist/ must exist before bundling).
#
# Regenerate the icon first if it changed:  python3 make_icon.py
# (writes icon-build/PartnerClient.icns; the committed PartnerClient.icns
#  is the canonical copy — refresh it from icon-build/ when you re-draw.)
#
# Output: packaging/out/dist/Partner Client.app  →  drag to /Applications.
# First-run config: the app reads --config, then $PARTNER_CLIENT_CONFIG,
# then ~/.partner-client/default-config (one line: path to a partner TOML).
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"

python3 -m PyInstaller --noconfirm --windowed \
  --name "Partner Client" \
  --icon "$(pwd)/PartnerClient.icns" \
  --paths "$REPO" \
  --add-data "$REPO/partner_client_gui/dist:dist" \
  --add-data "$REPO/partner_client/tools_builtin:partner_client/tools_builtin" \
  --collect-all tiktoken \
  --collect-all mcp \
  --hidden-import ollama \
  --distpath out/dist --workpath out/build --specpath out \
  "$REPO/partner_client_gui/launch.py"

echo
echo "Built: $(pwd)/out/dist/Partner Client.app"
