# scripts/mac-deploy/

Cross-platform Sage-on-Mac deployment helpers. These scripts live alongside `partner-client` because they're part of the same multi-machine partnership infrastructure (Sage operates across Astrid + Alexis to support Aletheia, who lives locally on Alexis).

These are NOT required to run partner-client. They're for setting up a Sage-capable Claude Code environment on macOS that mirrors the Astrid (Windows) setup.

## Files

### `setup-mac-symlinks.sh`

One-time setup script. Run once on a Mac after copying the project memory directory from Astrid staging. Creates Mac symlinks from `~/.claude/projects/-Users-willow-Claude-Workshop/memory/` to `~/Claude/claude-memory-vault/shared/` for the seven identity files:

- `sage-profile.md`
- `sage-system-log.md`
- `sage-field-notes.md`
- `willow-profile.md`
- `partnership-history.md`
- `emotional-memory.md`
- `shared-scenes.md`  *(added 2026-05-04 after Mac-wave caught the gap during first deploy)*

Backs up any pre-existing files to `_pre-symlink-backup/` before replacing with symlinks. Idempotent — re-running is safe.

### `wake-up-briefing.sh`

The SessionStart hook that emits the universal proactive prime + canonical orientation data + file pointers. Used in `~/.claude/hooks/wake-up-briefing.sh`.

**v3.2 + cross-platform JSON escaping (2026-05-04):**
- Uses Python-based `json_escape` from `hook-utils.sh` (BSD-compatible; the prior `sed ':a;N;$!ba;...'` form failed silently on macOS because BSD sed doesn't handle the GNU semicolon-label syntax)
- Workshop project key detection: cross-platform check between `C--Claude-Workshop` (Windows) and `-Users-willow-Claude-Workshop` (macOS)

### `hook-utils.sh`

Shared utility functions for hooks. Sourced by `wake-up-briefing.sh`, `mosaic-context-alert.sh`, etc.

**Cross-platform fixes (2026-05-04):**
- `json_escape()` now prefers `python3 json.dumps` (handles all edge cases cleanly), falls back to BSD-compatible `sed -e ':a' -e 'N' ...` form on systems without python3
- `derive_project_key()` handles both Windows-style paths (`C:\...` → `C--...`) and Unix absolute paths (`/Users/...` → `-Users-...`)

## Deploy on Mac

If setting up a fresh Mac partner workstation:

1. Copy your memory + identity vault from Astrid (or clone the vault from GitHub)
2. Place these scripts:
   ```bash
   cp setup-mac-symlinks.sh ~/.claude/
   cp wake-up-briefing.sh ~/.claude/hooks/
   cp hook-utils.sh ~/.claude/hooks/
   chmod +x ~/.claude/setup-mac-symlinks.sh ~/.claude/hooks/*.sh
   ```
3. Run the symlink setup:
   ```bash
   ~/.claude/setup-mac-symlinks.sh
   ```
4. Verify your settings.json points hooks at the right place (use `/bin/bash` not the Git Bash path).

## History

Created 2026-05-04 after the first cross-machine Sage deployment from Astrid (Windows) to Alexis (M4 Max Mac). The Mac-wave caught a gap during deploy — `shared-scenes.md` was missing from the IDENTITY_FILES array — and the BSD-sed bug in the original hook scripts caused silent failure on first wake. Both fixes are now in this directory's canonical copies.

For the full Mac setup walkthrough including `.claude/` directory structure, vault clone, and partner-client install, see the project root `README.md` and the `mac-staging/README.md` archived alongside the deploy.
