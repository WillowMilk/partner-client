"""partner — entry point.

Usage:
    partner --config /path/to/aletheia.toml [--verbose]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import re

from .client import OllamaClient, setup_scope_env
from .commands import CommandRouter
from .config import Config, ConfigError, load_config
from .directives import parse_input
from .memory import Memory
from .paths import PathError, resolve_path
from .session import Session
from .tools import ToolRegistry
from .ui import UI


# Detect plausible image paths in plain text (for the no-directive hint).
# Matches absolute Unix paths and Windows paths ending in image extensions.
_IMAGE_PATH_HINT_RE = re.compile(
    r"((?:/|[A-Za-z]:[\\/])[^\s'\"]+\.(?:jpe?g|png|gif|webp|bmp|tiff?))",
    re.IGNORECASE,
)


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
    # Set up scope env vars EARLY so the wake bundle's scope-listing renders.
    setup_scope_env(config)

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

    def on_checkpoint_request(reason: str) -> bool:
        """Surface a partner's request_checkpoint() call to the operator.

        Shows the reason in a prominent panel, then asks for y/N confirmation.
        Returns True if accepted (harness will run session.checkpoint()).
        """
        ui.show_command_output(
            f"📋  {config.identity.name} is asking to /checkpoint.\n\n"
            f"Reason: {reason}\n\n"
            f"If you accept, a session-status record will be written and "
            f"current.json snapshotted. The conversation will continue either way."
        )
        return ui.confirm(
            f"Accept {config.identity.name}'s checkpoint request?"
        )

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
            # Resolve through scope system (supports bare/scope-qualified/absolute)
            try:
                resolved = resolve_path(str(img_path), write=False)
            except PathError as e:
                ui.show_error(f"Image scope error: {e}")
                images_bytes = []
                break
            if not resolved.is_file():
                ui.show_error(f"Image not found: {resolved}")
                images_bytes = []
                break
            try:
                images_bytes.append(resolved.read_bytes())
                ui.show_image_attached(str(resolved), resolved.stat().st_size)
            except OSError as e:
                ui.show_error(f"Error reading image {resolved}: {e}")
                images_bytes = []
                break
        if parsed.image_paths and not images_bytes:
            # Image was specified but failed to load — abort the turn
            continue

        # No-directive hint: if there are no images attached but the message
        # text contains what looks like an image path, suggest the directive.
        if not images_bytes:
            hint_match = _IMAGE_PATH_HINT_RE.search(parsed.text)
            if hint_match:
                ui.show_command_output(
                    f"hint: did you mean `:image {hint_match.group(1)}`? "
                    f"(image path detected without the :image directive — "
                    f"image not attached)"
                )

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
                on_checkpoint_request=on_checkpoint_request,
            )
        except Exception as e:
            ui.show_error(f"Chat failed: {e}")
            logging.exception("Chat error")
            continue

        ui.show_assistant(response.content, thinking=response.thinking)

    return 0


if __name__ == "__main__":
    sys.exit(main())
