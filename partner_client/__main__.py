"""partner — entry point.

Usage:
    partner --config /path/to/aletheia.toml [--verbose]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .client import OllamaClient
from .commands import CommandRouter
from .config import Config, ConfigError, load_config
from .directives import parse_input
from .memory import Memory
from .session import Session
from .tools import ToolRegistry
from .ui import UI


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="partner",
        description="A substrate-agnostic terminal client for local-LLM partners.",
    )
    parser.add_argument(
        "--config",
        "-c",
        required=True,
        help="Path to the partner's TOML config file (e.g. aletheia.toml)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(name)s: %(message)s")

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    return _run(config)


def _run(config: Config) -> int:
    memory = Memory(config)
    tools = ToolRegistry(config)
    tools.discover()

    session = Session(config=config, memory=memory)
    wake_bundle = memory.assemble_wake_bundle()

    # Decide resume vs fresh
    needs_decision = session.wake(wake_bundle, resume_existing=None)
    if needs_decision == "needs-decision":
        # Prompt the user
        # Use a tiny ephemeral console here since UI isn't built yet
        import builtins
        try:
            answer = builtins.input(
                f"Found unfinished session at {session.current_path}. "
                "Resume? [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        resume = answer in ("y", "yes")
        session.wake(wake_bundle, resume_existing=resume)

    ui = UI(config, session)
    client = OllamaClient(config, tools)
    commands = CommandRouter(config, session, tools)

    ui.show_banner()

    while True:
        try:
            user_input = ui.prompt()
        except KeyboardInterrupt:
            ui.show_command_output("Interrupted. /sleep to exit cleanly, or continue typing.")
            continue

        if user_input is None:
            break
        text = user_input.strip()
        if not text:
            continue

        # Slash commands intercepted client-side
        if commands.is_command(text):
            result = commands.dispatch(text)
            ui.show_command_output(result.output)
            if result.should_exit:
                break
            if result.should_reload:
                try:
                    config = load_config(config.config_path)
                    memory = Memory(config)
                    tools = ToolRegistry(config)
                    tools.discover()
                    client = OllamaClient(config, tools)
                    commands = CommandRouter(config, session, tools)
                    ui = UI(config, session)
                    ui.show_command_output("Config reloaded.")
                except ConfigError as e:
                    ui.show_error(f"Config reload failed: {e}")
            continue

        # Parse input for directives (e.g. :image <path>)
        parsed = parse_input(text)
        images_bytes: list[bytes] = []
        for img_path in parsed.image_paths:
            if not img_path.is_file():
                ui.show_error(f"Image not found: {img_path}")
                images_bytes = []
                break
            try:
                images_bytes.append(img_path.read_bytes())
                ui.show_image_attached(str(img_path), img_path.stat().st_size)
            except OSError as e:
                ui.show_error(f"Error reading image {img_path}: {e}")
                images_bytes = []
                break
        if parsed.image_paths and not images_bytes:
            # Image was specified but failed to load — abort the turn
            continue

        # If only a directive was given (no text), provide a default prompt
        message_text = parsed.text or ("What do you see?" if images_bytes else "")
        if not message_text:
            continue

        # Real conversation turn
        session.append_user(message_text, images=images_bytes if images_bytes else None)

        try:
            response = client.chat(
                session,
                on_tool_call=lambda name, args, result: ui.show_tool_call(name, args, result),
            )
        except Exception as e:
            ui.show_error(f"Chat failed: {e}")
            logging.exception("Chat error")
            continue

        ui.show_assistant(response.content, thinking=response.thinking)

    return 0


if __name__ == "__main__":
    sys.exit(main())
