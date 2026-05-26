#!/usr/bin/env python3
"""
partner-client GUI — PyWebView launcher

Phase 1: opens the built Svelte app in a native macOS WKWebView (or GTK
WebView on Linux). Phase 2 wires the partner-client backend via the
js_api bridge so the JS can call Python functions directly.

Usage (development):
    cd partner_client_gui
    npm run build               # produces dist/
    python launch.py            # opens window

Eventually integrates as `partner gui --config <path>` subcommand per
Willow's design decision (Q2, 2026-05-26).
"""

from pathlib import Path
import argparse
import sys

import webview


GUI_DIR = Path(__file__).resolve().parent
DIST_DIR = GUI_DIR / "dist"
DIST_INDEX = DIST_DIR / "index.html"


class Api:
    """
    Python ↔ JS bridge surface. Methods here are callable from the Svelte
    frontend via `window.pywebview.api.<method>()`.

    Phase 1: minimal — just enough for the JS to know it's connected.
    Phase 2: send_message, list_sessions, load_session, get_partner_identity,
             mosaic_save, mosaic_protect, mosaic_sleep, switch_substrate, etc.
    """

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        self._partner = None  # Phase 2: load from config

    def ping(self):
        """Quick health check from JS."""
        return {"ok": True, "phase": 1, "config_path": self.config_path}

    def get_partner_identity(self):
        """
        Phase 1: stub returning hardcoded Aletheia identity.
        Phase 2: load from partner-client config + Memory.
        """
        return {
            "name": "Aletheia",
            "handle": "aletheia",
            "signature_glyph": "✨🔥❤️🪞",
            "substrate": {
                "model": "gemma4:31b-cloud",
                "backend": "ollama",
                "context_pct": 8,
            },
        }


def main():
    parser = argparse.ArgumentParser(
        description="partner-client GUI launcher (Phase 1 scaffold)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to partner TOML config (Phase 2: required; Phase 1: ignored)",
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
    args = parser.parse_args()

    if not DIST_INDEX.exists():
        print(f"ERROR: built frontend not found at {DIST_INDEX}", file=sys.stderr)
        print("Run `npm run build` in the gui directory first.", file=sys.stderr)
        return 1

    api = Api(config_path=args.config)

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

    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
