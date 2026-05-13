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
from .plans import PlanStore
from .session import Session
from .timeline import RunTimeline
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


def _estimate_resume_wait(size_kb: float) -> str:
    """Rough estimate of first-response latency for a full resume.

    Calibrated from the 2026-05-09 diagnostic on M4 Max + gemma4:31b:
    a 437 KB current.json (~31K tokens) took 20m28s for the first response
    after resume, ~30x slower than warm-KV subsequent calls. The bottleneck
    is Ollama rebuilding the KV cache from the full prompt; cost scales
    roughly linearly with token count. ~13K tokens per minute is the
    observed rate on that hardware.

    Numbers are deliberately wide-band ranges — actual latency depends on
    hardware, model size, num_ctx, and what else is on the GPU.
    """
    if size_kb < 100:
        return "~30s-1 min"
    if size_kb < 300:
        return "~1-3 min"
    if size_kb < 600:
        return "~3-6 min"
    if size_kb < 1000:
        return "~6-12 min"
    return "~12+ min"


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
        "--model",
        "-m",
        default=None,
        help=(
            "Override the TOML's [model] name for this session only "
            "(e.g. gemma4:31b-it-q8_0, gemma4:31b-it-bf16, gemma4:31b-cloud). "
            "TOML stays unmodified. Fails fast if model is not pulled locally."
        ),
    )
    parser.add_argument(
        "--choose-model",
        action="store_true",
        help=(
            "Show an interactive picker at startup listing locally-pulled "
            "models. Result overrides TOML for this session only. Cannot be "
            "combined with --model (--model wins if both are passed)."
        ),
    )
    parser.add_argument(
        "subcommand",
        nargs="?",
        default="chat",
        choices=["chat", "doctor", "distill"],
        help=(
            "What to do. 'chat' (default) opens an interactive session. "
            "'doctor' runs health checks against the config and exits. "
            "'distill' applies MOSAIC selective preservation to a session "
            "JSON (Phase 1: Pass 1 mechanical strip)."
        ),
    )
    # Capture any args after the subcommand for the subcommand's own parser
    args, distill_args = parser.parse_known_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(name)s: %(message)s")

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.subcommand == "doctor":
        from .doctor import run_doctor
        # Doctor still respects --model override so operators can validate
        # a specific variant without editing TOML.
        if args.model is not None:
            config.model.name = args.model
        return run_doctor(config)

    if args.subcommand == "distill":
        from .distill.cli import run_distill_cli
        return run_distill_cli(config, distill_args)

    # Resolve which model to use (chat path only — doctor/distill above are
    # subcommand short-circuits). CLI flag wins over interactive picker
    # wins over TOML default. Fail-fast on unavailable model.
    from .model_selector import resolve_active_model
    resolved_name, error = resolve_active_model(
        config_model_name=config.model.name,
        cli_override=args.model,
        use_interactive=args.choose_model,
    )
    if error is not None:
        print(error, file=sys.stderr)
        return 2
    config.model.name = resolved_name

    return _run(config)


def _run(config: Config) -> int:
    # Set up scope env vars EARLY so the wake bundle's scope-listing renders.
    setup_scope_env(config)

    memory = Memory(config)
    tools = ToolRegistry(config)
    tools.discover()

    session = Session(config=config, memory=memory)
    wake_bundle = memory.assemble_wake_bundle()

    # Decide resume vs truncate vs fresh. The 3-way prompt is shown only if
    # an existing non-closed current.json is found (otherwise wake() goes
    # straight to fresh). When `resume_keep_pairs` is 0, [t] is omitted.
    wake_status = session.wake(wake_bundle, resume_mode=None)
    if wake_status == "needs-decision":
        import builtins
        # Estimate size to show the operator when truncation actually matters
        try:
            size_bytes = session.current_path.stat().st_size
            size_kb = size_bytes / 1024
            size_label = f"{size_kb:,.0f} KB" if size_kb >= 1 else f"{size_bytes} bytes"
        except OSError:
            size_label = "unknown size"

        keep_pairs = config.wake_bundle.resume_keep_pairs
        truncation_available = keep_pairs > 0

        if truncation_available:
            prompt_text = (
                f"Found unfinished session at {session.current_path} ({size_label}).\n"
                f"  [y] Resume full       - complete granular recall (slow on heavy sessions)\n"
                f"  [t] Resume truncated  - keep last {keep_pairs} pairs + refreshed system msgs; full snapshot archived\n"
                f"  [n] Fresh wake        - archive existing, start a new session\n"
                f"Choice [y/t/n]: "
            )
            valid_choices = {"y", "yes", "t", "trunc", "truncate", "truncated", "n", "no"}
        else:
            prompt_text = (
                f"Found unfinished session at {session.current_path} ({size_label}). "
                f"Resume? [y/N] "
            )
            valid_choices = {"y", "yes", "n", "no"}

        try:
            answer = builtins.input(prompt_text).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("y", "yes"):
            resume_mode = "full"
        elif truncation_available and answer in ("t", "trunc", "truncate", "truncated"):
            resume_mode = "truncated"
        else:
            resume_mode = "fresh"

        # Heavy-resume warning: when the operator chose [y] on a session
        # larger than the configured threshold, surface the expected wait
        # time BEFORE wake() commits. The cost is real (KV cache rebuild)
        # and worth naming honestly so the silence during prefill isn't
        # mistaken for a hang. (2026-05-09 diagnosis: 20m28s on 437 KB.)
        if resume_mode == "full":
            try:
                resume_size_kb = session.current_path.stat().st_size / 1024
            except OSError:
                resume_size_kb = 0
            if resume_size_kb >= config.wake_bundle.heavy_resume_warn_kb:
                est = _estimate_resume_wait(resume_size_kb)
                hint = ""
                if truncation_available:
                    hint = (
                        f"   Use [t] at the prompt next time for a truncated resume "
                        f"(~3 min) that keeps recent thread + archives the full snapshot.\n"
                    )
                print(
                    f"\n📊 Resuming from ~{resume_size_kb:,.0f} KB of history.\n"
                    f"   First response will take {est} "
                    f"(Ollama is rebuilding the KV cache from this history).\n"
                    f"{hint}",
                    flush=True,
                )

        wake_status = session.wake(wake_bundle, resume_mode=resume_mode)

    ui = UI(config, session)
    timeline = RunTimeline(config, session)
    timeline.record(
        "session_wake",
        status=wake_status,
        wake_bundle_chars=len(wake_bundle.system_prompt),
        recent_message_count=len(wake_bundle.recent_messages),
        context_tokens=session.estimate_tokens(),
    )
    plan_store = PlanStore(config)
    client = OllamaClient(config, tools, timeline=timeline)
    commands = CommandRouter(config, session, tools)

    def on_checkpoint_request(reason: str) -> tuple[bool, str | None]:
        """Surface a partner's request_checkpoint() call to the operator.

        Shows the reason in a prominent panel, then offers three-option
        consent (y / n / typed-response). Returns (accepted, optional_message)
        — when the operator types a response instead of y/n, that message
        flows back to the partner as the tool result.
        """
        ui.show_command_output(
            f"📋  {config.identity.name} is asking to /checkpoint.\n\n"
            f"Reason: {reason}\n\n"
            f"If you accept, a session-status record will be written and "
            f"current.json snapshotted. The conversation will continue either way."
        )
        timeline.record("checkpoint_requested", reason=reason)
        decision = ui.confirm_with_response(
            f"Accept {config.identity.name}'s checkpoint request?"
        )
        timeline.record(
            "checkpoint_decision",
            accepted=decision[0],
            custom_message=bool(decision[1]),
        )
        return decision

    def on_plan_approval_request(summary: str, plan: list[str]) -> tuple[bool, str | None]:
        """Surface a partner's request_plan_approval() call to the operator.

        Shows the plan summary + numbered steps, then offers three-option
        consent. Returns (accepted, optional_message). A typed response
        replaces the canned decline with the operator's own voice.
        """
        plan_record = None
        try:
            plan_record = plan_store.create(summary, plan, session.session_num)
            timeline.record(
                "plan_proposed",
                plan_id=plan_record["id"],
                summary=summary,
                step_count=len(plan),
            )
        except OSError as e:
            ui.show_error(f"Could not persist durable plan record: {e}")
            timeline.record("plan_persist_error", summary=summary, error=str(e))

        plan_lines = "\n".join(f"  {i + 1}. {step}" for i, step in enumerate(plan))
        plan_id_line = f"Plan ID: {plan_record['id']}\n\n" if plan_record else ""
        ui.show_command_output(
            f"📋  {config.identity.name} is proposing a plan:\n\n"
            f"{plan_id_line}"
            f"\"{summary}\"\n\n"
            f"{plan_lines}\n\n"
            f"If you approve, the partner will proceed with these steps in "
            f"their next turns. If you decline, no actions are taken; the "
            f"conversation continues."
        )
        decision = ui.confirm_with_response(
            f"Approve {config.identity.name}'s plan?"
        )
        if plan_record is not None:
            try:
                decided = plan_store.decide(
                    plan_record["id"],
                    accepted=decision[0],
                    operator_message=decision[1],
                )
                timeline.record(
                    "plan_decision",
                    plan_id=plan_record["id"],
                    status=decided["status"],
                    custom_message=bool(decision[1]),
                )
            except OSError as e:
                ui.show_error(f"Could not update durable plan decision: {e}")
                timeline.record(
                    "plan_decision_persist_error",
                    plan_id=plan_record["id"],
                    error=str(e),
                )
        return decision

    def on_git_push_request(
        repo: str,
        remote_url: str,
        commits: list[str],
    ) -> tuple[bool, str | None]:
        """Surface a partner's git_push call (off-allowlist) to the operator.

        Called only when the remote URL is NOT in config.git.push_allowlist.
        Allowlisted pushes auto-approve and never reach this callback. Returns
        (accepted, optional_message) — typed response flows back as the tool
        result so a redirect can carry the operator's voice rather than read
        as substrate refusal.
        """
        timeline.record(
            "git_push_requested",
            repo=repo,
            remote_url=remote_url,
            commit_count=len(commits),
        )
        commit_lines = "\n".join(f"  {c}" for c in commits) if commits else "  (none)"
        ui.show_command_output(
            f"📋  {config.identity.name} is asking to git_push: {repo}\n\n"
            f"Remote URL: {remote_url}\n"
            f"  ⚠ This URL is NOT in your push_allowlist.\n\n"
            f"Pending commits:\n{commit_lines}\n\n"
            f"If you approve, the push proceeds. If you decline silently, "
            f"the push is skipped. If you type a response, that text flows "
            f"back to the partner as the tool result."
        )
        decision = ui.confirm_with_response(
            f"Approve {config.identity.name}'s push to {remote_url}?"
        )
        timeline.record(
            "git_push_decision",
            repo=repo,
            remote_url=remote_url,
            accepted=decision[0],
            custom_message=bool(decision[1]),
        )
        return decision

    def on_delete_path_request(
        target,
        recursive: bool,
        summary: str,
    ) -> tuple[bool, str | None]:
        """Surface a partner's delete_path call to the operator.

        By design, every delete_path invocation reaches this callback —
        there is no allowlist short-circuit. Returns (accepted,
        optional_message); a typed response flows back to the partner
        as the tool result so a decline can carry redirection or care.

        Timeline events for delete_path_requested / delete_path_decision
        are recorded by the client's special-case dispatch (where the
        path is canonicalized after pre-flight). This callback only
        renders the prompt and returns the decision.
        """
        recursive_label = " (RECURSIVELY)" if recursive else ""
        ui.show_command_output(
            f"🗑️   {config.identity.name} is asking to delete{recursive_label}:\n\n"
            f"  {target}\n\n"
            f"  This is a {summary}.\n\n"
            f"If you approve, the path is removed. If you decline silently, "
            f"nothing is changed. If you type a response, that text flows "
            f"back to the partner as the tool result."
        )
        return ui.confirm_with_response(
            f"Approve {config.identity.name}'s delete of {target}?"
        )

    # Pre-warm: load the model into VRAM BEFORE the prompt opens so cold-load
    # latency is visible startup cost rather than invisible mid-conversation
    # cost on the first turn. Failures are non-fatal — the substrate check
    # `partner doctor` already validates ollama reachability + model
    # availability, so a prewarm failure here means real trouble that the
    # first chat call will surface to the operator in context.
    if config.wake_bundle.prewarm_on_startup:
        print(
            f"🔥 Warming {config.identity.name}'s substrate ({config.model.name})... ",
            end="",
            flush=True,
        )
        ok, elapsed, err = client.prewarm()
        if ok:
            print(f"✓ {elapsed:.1f}s", flush=True)
        else:
            print(f"✗ skipped ({err})", flush=True)
            print(
                "   (Pre-warm failed but startup continues; the first real "
                "chat call will surface the underlying error if it persists.)",
                flush=True,
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
            timeline.record(
                "slash_command",
                command=text.split(maxsplit=1)[0],
                input_chars=len(text),
                should_exit=result.should_exit,
                should_reload=result.should_reload,
            )
            ui.show_command_output(result.output)
            if result.should_exit:
                break
            if result.should_reload:
                try:
                    config = load_config(config.config_path)
                    memory = Memory(config)
                    tools = ToolRegistry(config)
                    tools.discover()
                    timeline = RunTimeline(config, session)
                    plan_store = PlanStore(config)
                    client = OllamaClient(config, tools, timeline=timeline)
                    # Rebind session to the new config + memory so checkpoint/sleep
                    # write to the new locations rather than the stale ones.
                    session.config = config
                    session.memory = memory
                    commands = CommandRouter(config, session, tools)
                    ui = UI(config, session)
                    timeline.record("config_reloaded")
                    ui.show_command_output("Config reloaded.")
                except ConfigError as e:
                    ui.show_error(f"Config reload failed: {e}")
                    timeline.record("config_reload_error", error=str(e))
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
        timeline.record(
            "user_message",
            chars=len(message_text),
            images=len(images_bytes),
            context_tokens=session.estimate_tokens(),
        )

        try:
            response = client.chat(
                session,
                ui=ui,
                on_checkpoint_request=on_checkpoint_request,
                on_plan_approval_request=on_plan_approval_request,
                on_git_push_request=on_git_push_request,
                on_delete_path_request=on_delete_path_request,
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
            timeline.record(
                "generation_cancelled",
                context_tokens=session.estimate_tokens(),
            )
            continue
        except Exception as e:
            ui.cancel_stream()
            ui.show_error(f"Chat failed: {e}")
            logging.exception("Chat error")
            timeline.record("chat_error", error=str(e))
            continue

        # Content was already rendered during streaming. Render thinking after,
        # if configured. (Old ui.show_assistant is no longer called; streaming
        # owns the content display.)
        if response.thinking and config.ui.show_thinking:
            ui.show_thinking(response.thinking)

    return 0


if __name__ == "__main__":
    sys.exit(main())
