"""partner — entry point.

Usage:
    partner --config /path/to/aletheia.toml [--verbose]
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .client import OllamaClient, setup_scope_env
from .commands import CommandRouter
from .config import Config, ConfigError, load_config
from .directives import parse_input
from .memory import Memory
from .paths import PathError, resolve_path
from .session import Session
from .tools import ToolRegistry
from .ui import UI


# Detect plausible image paths in plain text for implicit auto-attachment.
# Matches absolute Unix paths, tilde-prefixed paths, and Windows paths ending
# in known image extensions. Three alternatives, in order:
#   1. Single-quoted path (allows spaces inside the quotes)
#   2. Double-quoted path (allows spaces inside the quotes)
#   3. Bare path (no spaces, no quotes)
# Each alternative captures the bare path text without surrounding quotes.
# Scope-qualified forms (e.g. "memory:foo.jpg") are not matched here —
# those flow through the explicit :image directive.
_IMAGE_PATH_AUTO_RE = re.compile(
    r"""
    '(?P<sq>(?:~|/|[A-Za-z]:[\\/])[^']+\.(?:jpe?g|png|gif|webp|bmp|tiff?|heic))'
    |
    "(?P<dq>(?:~|/|[A-Za-z]:[\\/])[^"]+\.(?:jpe?g|png|gif|webp|bmp|tiff?|heic))"
    |
    (?P<bare>(?:~|/|[A-Za-z]:[\\/])[^\s'"]+\.(?:jpe?g|png|gif|webp|bmp|tiff?|heic))
    """,
    re.IGNORECASE | re.VERBOSE,
)

_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic",
}


def _is_image_extension(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTENSIONS


def _read_clipboard_image() -> bytes | None:
    """Read an image from the system clipboard. Returns None when unsupported or empty.

    macOS: uses `pbpaste -Prefer public.png`. Falls back to `tiff` if `png` is empty.
    Other platforms: returns None (graceful — :clip prints a friendly error).
    """
    if sys.platform != "darwin":
        return None
    for prefer in ("public.png", "public.jpeg", "public.tiff"):
        try:
            res = subprocess.run(
                ["pbpaste", "-Prefer", prefer],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        data = res.stdout
        if not data:
            continue
        # Validate image magic bytes — pbpaste returns text for non-image clipboards
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return data
        if data.startswith(b"\xff\xd8\xff"):  # JPEG
            return data
        if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):  # TIFF
            return data
    return None


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
    parser.add_argument(
        "subcommand",
        nargs="?",
        default="chat",
        choices=["chat", "doctor"],
        help=(
            "What to do. 'chat' (default) opens an interactive session. "
            "'doctor' runs health checks against the config and exits."
        ),
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(name)s: %(message)s")

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.subcommand == "doctor":
        from .doctor import run_doctor
        return run_doctor(config)

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
                    # Rebind session to the new config + memory so checkpoint/sleep
                    # write to the new locations rather than the stale ones.
                    session.config = config
                    session.memory = memory
                    commands = CommandRouter(config, session, tools)
                    ui = UI(config, session)
                    ui.show_command_output("Config reloaded.")
                except ConfigError as e:
                    ui.show_error(f"Config reload failed: {e}")
            continue

        # Parse input for explicit :image and :clip directives.
        parsed = parse_input(text)
        images_bytes: list[bytes] = []
        attachment_failed = False

        for img_path in parsed.image_paths:
            # Resolve through scope system (supports bare/scope-qualified/absolute)
            try:
                resolved = resolve_path(str(img_path), write=False)
            except PathError as e:
                ui.show_error(f"Image scope error: {e}")
                attachment_failed = True
                break
            if not resolved.is_file():
                ui.show_error(f"Image not found: {resolved}")
                attachment_failed = True
                break
            try:
                data = resolved.read_bytes()
                images_bytes.append(data)
                ui.show_image_attached(str(resolved), resolved.stat().st_size, image_bytes=data)
            except OSError as e:
                ui.show_error(f"Error reading image {resolved}: {e}")
                attachment_failed = True
                break

        # Clipboard image directive (`:clip`)
        if parsed.clipboard_image:
            clip_bytes = _read_clipboard_image()
            if clip_bytes is None:
                ui.show_error(
                    "No image in clipboard, or clipboard image read isn't supported "
                    "on this platform (currently macOS-only via pbpaste)."
                )
                attachment_failed = True
            else:
                # Persist a copy so the image isn't only in volatile session memory.
                ext = ".png" if clip_bytes.startswith(b"\x89PNG") else (
                    ".jpg" if clip_bytes.startswith(b"\xff\xd8\xff") else ".tiff"
                )
                tmp = Path(tempfile.gettempdir()) / f"{config.identity.name.lower()}-clip-{int(time.time())}{ext}"
                try:
                    tmp.write_bytes(clip_bytes)
                except OSError:
                    pass  # non-fatal; we still attach the bytes
                images_bytes.append(clip_bytes)
                ui.show_image_attached(str(tmp), len(clip_bytes), image_bytes=clip_bytes)

        if attachment_failed:
            # Explicit directive failed — abort the turn rather than send a half-attached message.
            continue

        # Implicit detection: when no :image directive was used, scan the message
        # text for image paths and auto-attach any that resolve to existing files
        # within an allowed scope. Failures are silent — the path may have been
        # mentioned without intent to attach (e.g. discussing a file by name).
        if not images_bytes:
            seen_paths: set[Path] = set()
            for match in _IMAGE_PATH_AUTO_RE.finditer(parsed.text):
                candidate_str = match.group("sq") or match.group("dq") or match.group("bare")
                if not candidate_str:
                    continue
                out_of_scope = False
                try:
                    candidate = resolve_path(candidate_str, write=False)
                except PathError:
                    # Out of scope — but the user typed the path explicitly,
                    # which is consent for *this* path (matches :image directive
                    # behavior). Resolve directly and attach with a teaching notice.
                    try:
                        candidate = Path(candidate_str).expanduser().resolve(strict=False)
                    except (OSError, RuntimeError):
                        continue
                    out_of_scope = True
                if candidate in seen_paths:
                    continue
                if not candidate.is_file() or not _is_image_extension(candidate):
                    continue
                try:
                    data = candidate.read_bytes()
                except OSError:
                    continue
                images_bytes.append(data)
                seen_paths.add(candidate)
                ui.show_image_attached(str(candidate), candidate.stat().st_size, image_bytes=data)
                if out_of_scope:
                    ui.show_command_output(
                        f"Note: '{candidate}' is outside your configured scopes. "
                        "Attached anyway because you typed the path explicitly. "
                        "Add a [[tool_paths]] entry in your TOML to silence this notice."
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
                ui=ui,
                on_checkpoint_request=on_checkpoint_request,
            )
        except KeyboardInterrupt:
            # User cancelled mid-generation. Close any open Live region cleanly,
            # record a partial-turn marker so the model knows the previous turn
            # was interrupted (avoids consecutive user-turn confusion next prompt).
            ui.cancel_stream()
            ui.show_command_output(
                "Generation cancelled. The conversation continues — type to send another message."
            )
            session.append_assistant(
                content="(Generation interrupted by Willow.)",
                thinking=None,
            )
            continue
        except Exception as e:
            ui.cancel_stream()
            ui.show_error(f"Chat failed: {e}")
            logging.exception("Chat error")
            continue

        # Content was already rendered during streaming. Render thinking after,
        # if configured. (Old ui.show_assistant is no longer called; streaming
        # owns the content display.)
        if response.thinking and config.ui.show_thinking:
            ui.show_thinking(response.thinking)

    return 0


if __name__ == "__main__":
    sys.exit(main())
