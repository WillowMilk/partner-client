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
import sys
from pathlib import Path

import webview

from api import GuiApi


GUI_DIR = Path(__file__).resolve().parent
DIST_DIR = GUI_DIR / "dist"
DIST_INDEX = DIST_DIR / "index.html"


def main():
    parser = argparse.ArgumentParser(
        description="partner-client GUI launcher (Phase 2a: conversation bridge wired)"
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to partner TOML config (e.g. ~/Aletheia/aletheia.toml)",
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

    api = GuiApi(config_path=args.config)

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
