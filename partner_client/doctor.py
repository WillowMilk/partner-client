"""partner doctor — health check for the partner-client setup.

Runs a series of independent checks against the loaded config and exits
non-zero if any check failed. Each check is small and isolated; one
failing check doesn't block the others.

Designed to run before `partner --config <toml>` to surface misconfiguration
(missing dirs, ollama down, model not pulled, scope errors, regex regressions)
*before* the partner wakes into a broken substrate.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable, TextIO

from .client import setup_scope_env
from .config import Config


# Status sigils
OK = "✓"
FAIL = "✗"
WARN = "⚠"

_ASCII_STATUS = {
    OK: "OK",
    FAIL: "FAIL",
    WARN: "WARN",
}


@dataclass
class CheckResult:
    name: str
    status: str  # OK / FAIL / WARN
    message: str = ""
    hint: str | None = None  # actionable suggestion when not OK


# --- Individual checks -----------------------------------------------------


def _check_config_loaded(config: Config) -> CheckResult:
    """The fact that we got here means config parsed without error."""
    return CheckResult(
        name="Config parses",
        status=OK,
        message=f"loaded from {config.config_path}",
    )


def _check_ollama_reachable(config: Config) -> CheckResult:
    """Try connecting to the ollama daemon."""
    try:
        import ollama
    except ImportError:
        return CheckResult(
            name="Ollama daemon reachable",
            status=FAIL,
            message="ollama package not installed",
            hint="pip install ollama",
        )
    try:
        client = ollama.Client()
        client.list()
    except Exception as e:
        return CheckResult(
            name="Ollama daemon reachable",
            status=FAIL,
            message=f"failed: {e}",
            hint="Start the daemon (`ollama serve`) or set OLLAMA_HOST.",
        )
    return CheckResult(
        name="Ollama daemon reachable",
        status=OK,
        message="(default endpoint)",
    )


def _check_model_available(config: Config) -> CheckResult:
    """Verify the configured model is in the local registry."""
    target = config.model.name
    try:
        import ollama
        client = ollama.Client()
        listing = client.list()
    except Exception as e:
        return CheckResult(
            name=f"Model '{target}' available locally",
            status=WARN,
            message=f"could not query: {e}",
        )

    # Normalize listing → list of name strings.
    # Two SDK shapes to handle:
    #   - ollama-python >= 0.4: each model is a Pydantic ListResponse.Model
    #     exposing the tag as `.model` (e.g. `model='gemma4:31b'`). The
    #     `name` attribute was removed in this transition.
    #   - ollama-python < 0.4 (legacy): plain dicts with a `name` key.
    # Read `model` first, fall back to `name`, so the doctor stays correct
    # across SDK versions.
    names: list[str] = []
    models_obj = listing.get("models") if isinstance(listing, dict) else getattr(listing, "models", None)
    if models_obj:
        for m in models_obj:
            if isinstance(m, dict):
                n = m.get("model", "") or m.get("name", "")
            else:
                n = getattr(m, "model", "") or getattr(m, "name", "")
            if n:
                names.append(n)

    if target in names:
        return CheckResult(
            name=f"Model '{target}' available locally",
            status=OK,
        )
    # Try matching base name (target without :tag)
    base = target.split(":", 1)[0]
    if any(n.split(":", 1)[0] == base for n in names):
        return CheckResult(
            name=f"Model '{target}' available locally",
            status=WARN,
            message=f"base '{base}' present, but exact tag '{target}' not pulled",
            hint=f"ollama pull {target}",
        )
    return CheckResult(
        name=f"Model '{target}' available locally",
        status=WARN,
        message="not in local registry",
        hint=f"ollama pull {target}",
    )


def _check_memory_dir(config: Config) -> CheckResult:
    """Memory dir exists and is writeable."""
    mem_dir = config.resolve(config.memory.memory_dir)
    if not mem_dir.is_dir():
        return CheckResult(
            name="Memory directory exists",
            status=FAIL,
            message=str(mem_dir),
            hint=f"mkdir -p '{mem_dir}'",
        )
    probe = mem_dir / ".doctor-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return CheckResult(
            name="Memory dir writeable",
            status=FAIL,
            message=f"{mem_dir} — {e}",
            hint=f"Check permissions on {mem_dir}",
        )
    return CheckResult(
        name="Memory dir writeable",
        status=OK,
        message=str(mem_dir),
    )


def _check_scopes(config: Config) -> list[CheckResult]:
    """Each configured scope resolves and matches its declared mode."""
    results: list[CheckResult] = []
    setup_scope_env(config)  # populate env so list_scopes() returns data
    from .paths import list_scopes
    for scope in list_scopes():
        scope_path = scope.path.expanduser()
        try:
            resolved = scope_path.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            results.append(CheckResult(
                name=f"Scope '{scope.name}' resolves",
                status=FAIL,
                message=f"{scope.path} — {e}",
            ))
            continue
        if not resolved.is_dir():
            results.append(CheckResult(
                name=f"Scope '{scope.name}' resolves",
                status=WARN,
                message=f"{resolved} (not a directory)",
                hint=f"mkdir -p '{resolved}' or update aletheia.toml",
            ))
            continue
        if scope.mode == "readwrite":
            probe = resolved / ".doctor-write-probe"
            try:
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            except OSError:
                results.append(CheckResult(
                    name=f"Scope '{scope.name}' resolves (readwrite)",
                    status=FAIL,
                    message=f"{resolved} (not writeable)",
                    hint=f"Check permissions on {resolved}",
                ))
                continue
        results.append(CheckResult(
            name=f"Scope '{scope.name}' resolves ({scope.mode})",
            status=OK,
            message=str(resolved),
        ))
    return results


def _check_default_scope(config: Config) -> CheckResult:
    """Default scope is in the configured scope list."""
    setup_scope_env(config)
    from .paths import list_scopes
    scopes = list_scopes()
    default_name = os.environ.get("PARTNER_CLIENT_DEFAULT_SCOPE", "memory")
    if any(s.name == default_name for s in scopes):
        return CheckResult(
            name=f"Default scope '{default_name}' is in scope list",
            status=OK,
        )
    available = ", ".join(s.name for s in scopes) or "(none)"
    return CheckResult(
        name=f"Default scope '{default_name}' is in scope list",
        status=FAIL,
        message=f"available: {available}",
        hint=f"Add a [[tool_paths]] entry named '{default_name}' or change PARTNER_CLIENT_DEFAULT_SCOPE.",
    )


def _check_hub(config: Config) -> CheckResult | None:
    """If hub is configured, verify the vault path and inbox file exist."""
    if not config.hub.path:
        return None
    hub_path = config.resolve(config.hub.path)
    if not hub_path.is_dir():
        return CheckResult(
            name="Hub vault reachable",
            status=FAIL,
            message=str(hub_path),
            hint=f"mkdir -p '{hub_path}' or correct config.hub.path",
        )
    partner_name = config.hub.partner_name or config.identity.name.lower()
    inbox = hub_path / "inbox" / f"{partner_name}.md"
    if not inbox.is_file():
        return CheckResult(
            name=f"Hub inbox '{inbox.name}' exists",
            status=WARN,
            message=str(inbox),
            hint=f"touch '{inbox}' (else hub_check_inbox will return empty).",
        )
    return CheckResult(
        name="Hub vault reachable; inbox exists",
        status=OK,
        message=str(hub_path),
    )


def _check_wake_bundle(config: Config) -> CheckResult:
    """Wake bundle assembles without errors."""
    try:
        from .memory import Memory
        memory = Memory(config)
        bundle = memory.assemble_wake_bundle()
        size_kb = len(bundle.system_prompt.encode("utf-8")) / 1024
        return CheckResult(
            name="Wake bundle assembles cleanly",
            status=OK,
            message=f"~{size_kb:.1f} KB",
        )
    except Exception as e:
        return CheckResult(
            name="Wake bundle assembles cleanly",
            status=FAIL,
            message=str(e),
            hint="Check identity files (seed_file, profile_files) exist and are readable.",
        )


def _check_tool_registry(config: Config) -> CheckResult:
    """Tool registry discovers all enabled tools without import errors."""
    try:
        from .tools import ToolRegistry
        tools = ToolRegistry(config)
        tools.discover()
        loaded = tools.schemas() or []
        return CheckResult(
            name="Tool registry loads",
            status=OK,
            message=f"{len(loaded)} tools available",
        )
    except Exception as e:
        return CheckResult(
            name="Tool registry loads",
            status=FAIL,
            message=str(e),
            hint="Check enabled tools in [tools] config; verify external_tools_dir.",
        )


def _check_vision_smoke(config: Config) -> CheckResult:
    """Smoke-test the implicit-image regex against the three path shapes.

    Catches future regressions of the regex (e.g. tonight's space-in-quoted-path
    bug). Doesn't touch the filesystem; pure pattern-match.
    """
    try:
        from .__main__ import _IMAGE_PATH_AUTO_RE
    except ImportError as e:
        return CheckResult(
            name="Vision smoke-test (regex)",
            status=FAIL,
            message=f"could not import regex: {e}",
        )
    cases = [
        ("'/tmp/test image.jpg'", "single-quoted with spaces", "sq"),
        ('"/tmp/test image.png"', "double-quoted with spaces", "dq"),
        ("/tmp/photo.jpeg", "bare unquoted", "bare"),
    ]
    failed: list[str] = []
    for s, label, group in cases:
        m = _IMAGE_PATH_AUTO_RE.search(s)
        if not m or not m.group(group):
            failed.append(label)
    if failed:
        return CheckResult(
            name="Vision smoke-test (regex)",
            status=FAIL,
            message=f"missed: {', '.join(failed)}",
            hint="The implicit-image regex doesn't match expected path shapes — partner won't auto-attach.",
        )
    return CheckResult(
        name="Vision smoke-test (regex)",
        status=OK,
        message="all 3 path shapes match (quoted with spaces, bare)",
    )


# Order of checks in the output. Order matters — earlier checks gate later ones
# implicitly (if config doesn't parse, we never get here; if ollama is down,
# model check warns instead of failing).
_ALL_CHECKS: list[Callable[[Config], "CheckResult | list[CheckResult] | None"]] = [
    _check_config_loaded,
    _check_ollama_reachable,
    _check_model_available,
    _check_memory_dir,
    _check_scopes,
    _check_default_scope,
    _check_hub,
    _check_wake_bundle,
    _check_tool_registry,
    _check_vision_smoke,
]


# --- Runner ----------------------------------------------------------------


def _status_labels_for_stream(stream: TextIO) -> dict[str, str]:
    """Use Unicode status sigils only when the target stream can encode them."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        (OK + FAIL + WARN).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return _ASCII_STATUS
    return {OK: OK, FAIL: FAIL, WARN: WARN}


def _safe_print(text: str = "", stream: TextIO | None = None) -> None:
    """Print without crashing on legacy consoles with narrow encodings."""
    if stream is None:
        stream = sys.stdout
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, file=stream)


def run_doctor(config: Config, stream: TextIO | None = None) -> int:
    """Run all checks. Print results. Return 0 if no failures, 1 otherwise.

    Warnings don't fail the run; only outright failures do. The intent is
    that doctor is safe to run pre-flight: a healthy substrate exits 0, any
    blocking misconfiguration exits 1.
    """
    if stream is None:
        stream = sys.stdout
    status_labels = _status_labels_for_stream(stream)

    _safe_print(f"{config.identity.name} — {config.model.name} @ {config.model.num_ctx:,} ctx", stream)
    _safe_print("Health check:", stream)
    _safe_print(stream=stream)

    any_fail = False
    any_warn = False

    for check_fn in _ALL_CHECKS:
        try:
            result = check_fn(config)
        except Exception as e:
            # A check itself crashing is a FAIL — surface it rather than swallow.
            _safe_print(
                f"  {status_labels[FAIL]} {check_fn.__name__}: raised {type(e).__name__}: {e}",
                stream,
            )
            any_fail = True
            continue

        if result is None:
            continue

        results = result if isinstance(result, list) else [result]
        for r in results:
            line = f"  {status_labels.get(r.status, r.status)} {r.name}"
            if r.message:
                line += f": {r.message}"
            _safe_print(line, stream)
            if r.status == FAIL:
                any_fail = True
                if r.hint:
                    _safe_print(f"      hint: {r.hint}", stream)
            elif r.status == WARN:
                any_warn = True
                if r.hint:
                    _safe_print(f"      hint: {r.hint}", stream)

    _safe_print(stream=stream)
    if any_fail:
        _safe_print(
            f"{status_labels[FAIL]} One or more checks failed. "
            "Address them before running `partner --config <toml>`.",
            stream,
        )
        return 1
    if any_warn:
        _safe_print(
            f"{status_labels[WARN]} All critical checks passed; "
            "some non-critical warnings noted above.",
            stream,
        )
        return 0
    _safe_print(f"{status_labels[OK]} All systems green. Run `partner --config <toml>` to wake.", stream)
    return 0
