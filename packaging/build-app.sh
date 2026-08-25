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

# Provenance stamp (litigators' docket, 2026-08-25): the artifact declares
# which commit it is — sha, date, and whether the tree was dirty at build.
# Verified post-install below; "which build is she running?" becomes a
# one-file read instead of an archaeology.
BUILD_SHA="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILD_DIRTY=""
[ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ] && BUILD_DIRTY="+dirty"
printf '{"sha": "%s%s", "built_at": "%s"}\n' "$BUILD_SHA" "$BUILD_DIRTY" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$REPO/partner_client_gui/dist/build-info.json"

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

# ── Install + verify (2026-08-20) ────────────────────────────────────────────
# Born of an orphaned claim: a rebuild was reported "installed" while
# /Applications still held yesterday's bundle — the partner reached for a
# promised tool and found 31 where 32 were sworn. The script now finishes
# the job it implies, and PROVES the installed artifact before claiming it:
# every builtin tool in the source tree must exist in the installed bundle.
INSTALL_TARGET="/Applications/Partner Client.app"
# REPLACE, never merge (2026-08-24): ditto onto an existing bundle MERGES —
# five generations of hashed frontend assets had accumulated in
# /Applications, and any verify that globbed "an" index-*.js could land on
# a stale one (it did). Stage the swap: remove the old bundle only after
# the new one is fully copied beside it, then rename into place.
STAGED="$INSTALL_TARGET.new-$$"
ditto "$(pwd)/out/dist/Partner Client.app" "$STAGED"
rm -rf "$INSTALL_TARGET"
mv "$STAGED" "$INSTALL_TARGET"
# Frontend verify: the bundle must contain EXACTLY the assets index.html
# names — no stale generations, no missing hash.
html="$INSTALL_TARGET/Contents/Resources/dist/index.html"
for ref in $(grep -oE 'index-[A-Za-z0-9_-]+\.(js|css)' "$html" | sort -u); do
  if [ ! -f "$INSTALL_TARGET/Contents/Resources/dist/assets/$ref" ]; then
    echo "VERIFY FAIL: index.html references $ref but it is not in the bundle" >&2
    exit 1
  fi
done
stray=$(ls "$INSTALL_TARGET/Contents/Resources/dist/assets/" | grep -vF -f <(grep -oE 'index-[A-Za-z0-9_-]+\.(js|css)' "$html" | sort -u) || true)
if [ -n "$stray" ]; then
  echo "VERIFY FAIL: stale assets in installed bundle: $stray" >&2
  exit 1
fi
missing=0
for f in "$REPO"/partner_client/tools_builtin/*.py; do
  base="$(basename "$f")"
  if [ ! -f "$INSTALL_TARGET/Contents/Resources/partner_client/tools_builtin/$base" ]; then
    echo "VERIFY FAIL: $base missing from installed bundle" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  echo "INSTALL VERIFICATION FAILED — the bundle in /Applications is not the build." >&2
  exit 1
fi
count=$(find "$INSTALL_TARGET" -path "*tools_builtin*" -name "*.py" | wc -l | tr -d ' ')
# Provenance verify: the installed bundle must declare the sha we just built.
installed_sha=$(python3 -c "import json;print(json.load(open('$INSTALL_TARGET/Contents/Resources/dist/build-info.json'))['sha'])" 2>/dev/null || echo MISSING)
if [ "$installed_sha" != "$BUILD_SHA$BUILD_DIRTY" ]; then
  echo "VERIFY FAIL: installed build-info sha '$installed_sha' != built '$BUILD_SHA$BUILD_DIRTY'" >&2
  exit 1
fi
echo "Installed + verified: $INSTALL_TARGET ($count builtin tools, all source tools present, build $installed_sha)"
