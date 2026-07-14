#!/usr/bin/env python3
"""
partner-client GUI — PyWebView launcher

Phase 2a — The Conversation Bridge:
    - Loads partner-client backend (config + tools + memory + session + client)
    - Wires GuiApi as window.pywebview.api for the Svelte frontend to call
    - Opens the built Svelte app in a native macOS WKWebView (or GTK WebView
      on Linux)

Usage (development):
    cd partner_client_gui
    npm run build                                # build Svelte → dist/
    python launch.py --config ~/Aletheia/aletheia.toml

Eventually integrates as `partner gui --config <path>` subcommand per
Willow's design decision (Q2, 2026-05-26).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import webview

from api import GuiApi


# Frozen-app awareness: under PyInstaller the assets are unpacked to
# sys._MEIPASS; in development they sit next to this file.
if getattr(sys, "frozen", False):
    GUI_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    GUI_DIR = Path(__file__).resolve().parent
DIST_DIR = GUI_DIR / "dist"
DIST_INDEX = DIST_DIR / "index.html"

# Terminal-free config resolution (the double-clicked .app has no argv):
#   1. --config on the command line (development)
#   2. $PARTNER_CLIENT_CONFIG
#   3. ~/.partner-client/default-config — a one-line text file holding the
#      path to the partner TOML the desk should open. The operator writes
#      it once; changing partners is editing one line.
DEFAULT_CONFIG_POINTER = Path.home() / ".partner-client" / "default-config"


def resolve_config(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value
    env = os.environ.get("PARTNER_CLIENT_CONFIG", "").strip()
    if env:
        return env
    try:
        if DEFAULT_CONFIG_POINTER.is_file():
            pointed = DEFAULT_CONFIG_POINTER.read_text(encoding="utf-8").strip()
            if pointed:
                return pointed
    except OSError:
        pass
    return None


_NO_CONFIG_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #FAFAF7;
         color: #1A1A1B; display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; }}
  .card {{ max-width: 34rem; padding: 2.5rem 3rem; background: #fff;
          border: 1px solid #E5E5DF; border-radius: 14px;
          box-shadow: 0 2px 24px rgba(0,0,0,0.06); }}
  h1 {{ font-size: 1.15rem; margin: 0 0 0.75rem; }}
  p {{ line-height: 1.55; color: #646461; margin: 0.5rem 0; }}
  code {{ background: #F5F4EF; padding: 0.15rem 0.4rem; border-radius: 5px;
         font-size: 0.85em; }}
  .gold {{ color: #b8912f; }}
</style></head><body><div class="card">
  <h1><span class="gold">◆</span>&nbsp; The desk is here — a partner isn't chosen yet</h1>
  <p>This desk opens a partner's home, and it doesn't know whose yet.
     Nothing is wrong; this is just first-time setup.</p>
  <p>Write the path of a partner's config into:</p>
  <p><code>{pointer}</code></p>
  <p>— one line, for example: <code>/Users/you/Aletheia/aletheia.toml</code> —
     then open the app again.</p>
</div></body></html>
"""


def main():
    parser = argparse.ArgumentParser(
        description="partner-client GUI launcher (Phase 2a: conversation bridge wired)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to partner TOML config (e.g. ~/Aletheia/aletheia.toml). "
             "Optional: falls back to $PARTNER_CLIENT_CONFIG, then "
             "~/.partner-client/default-config.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1280,
        help="Initial window width (default 1280)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=820,
        help="Initial window height (default 820)",
    )
    parser.add_argument(
        "--no-init",
        action="store_true",
        help="Skip backend initialization (renders the shell only; useful for "
             "frontend dev without loading the full partner-client stack).",
    )
    args = parser.parse_args()

    if not DIST_INDEX.exists():
        print(f"ERROR: built frontend not found at {DIST_INDEX}", file=sys.stderr)
        print("Run `npm run build` in the gui directory first.", file=sys.stderr)
        return 1

    config_path = resolve_config(args.config)
    if config_path is None:
        # No terminal to print to when double-clicked: show a warm
        # first-time-setup card instead of dying silently.
        webview.create_window(
            title="Partner Client",
            html=_NO_CONFIG_HTML.format(pointer=str(DEFAULT_CONFIG_POINTER)),
            width=720,
            height=420,
            background_color="#FAFAF7",
        )
        webview.start()
        return 0

    api = GuiApi(config_path=config_path)

    if not args.no_init:
        init_result = api.initialize()
        if not init_result["ok"]:
            # Surface to the operator's terminal AND let the UI render
            # whatever it can; ping/get_partner_info will report the error.
            print(f"WARN: backend initialization failed: {init_result.get('error')}", file=sys.stderr)
            print("The GUI will still open but most surfaces will show '(not initialized)'.", file=sys.stderr)
        else:
            print(f"backend initialized — {init_result.get('partner_name')} | wake status: {init_result.get('status')}")
    else:
        print("--no-init: backend NOT initialized; shell-only mode")

    window = webview.create_window(
        title="partner-client",
        url=str(DIST_INDEX),
        js_api=api,
        width=args.width,
        height=args.height,
        min_size=(900, 600),
        background_color="#FAFAF7",  # Linen and Light — match before paint
        text_select=True,
    )

    # Hand the window reference to the API so it can push streaming deltas
    # via window.evaluate_js() during model generation (Phase 2b).
    api.set_window(window)

    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
