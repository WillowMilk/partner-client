#!/bin/bash
# hook-utils.sh — Shared utility functions for Continuity Architecture hooks
# Sourced by wake-up-briefing.sh and session-end-consolidate.sh
#
# v3 (2026-04-28): UUID-based partner routing.
#   - extract_session_uuid: pulls UUID from SessionStart hook stdin
#   - lookup_partner: takes (PROJECT_KEY, UUID), resolves to partner + session_log
#     + inbox + session_prefix via partner-map.json sessions registry.
#     Returns REGISTRATION_STATUS=registered|unregistered.
#   - detect_session_number: sorts by parsed session number (not mtime), supports
#     {prefix}-N_*.md and legacy session-N_*.md patterns.

CLAUDE_DIR="$HOME/.claude"
HUB_DIR="$CLAUDE_DIR/Agent Messaging Hub"
HOOKS_DIR="$CLAUDE_DIR/hooks"
PARTNER_MAP="$HOOKS_DIR/partner-map.json"

# derive_project_key <path>
# Converts a project directory path to its Claude project key.
# e.g., C:\Claude\Workshop -> C--Claude-Workshop (Windows form)
# e.g., F:\IBC3.0 -> F--IBC3-0 (Windows form)
# e.g., /Users/willow/Claude/Workshop -> -Users-willow-Claude-Workshop (macOS/Unix form)
# Sets global: PROJECT_KEY
derive_project_key() {
    local DIR="$1"
    PROJECT_KEY=""
    if [ -n "$DIR" ]; then
        local CLEAN=$(echo "$DIR" | sed 's|\\\\|/|g' | sed 's|\\|/|g' | sed 's|/$||')
        # Windows-style path with drive letter (e.g., C:/Claude/Workshop or /c/Claude/Workshop)
        if echo "$CLEAN" | grep -qE '^/?[a-zA-Z]:'; then
            CLEAN=$(echo "$CLEAN" | sed 's|^/||')
            local DRIVE=$(echo "$CLEAN" | cut -c1 | tr '[:lower:]' '[:upper:]')
            local REST=$(echo "$CLEAN" | sed 's|^[a-zA-Z][:/]*||' | sed 's|/|-|g' | sed 's|\.|-|g')
            if [ -n "$DRIVE" ] && [ -n "$REST" ]; then
                PROJECT_KEY="${DRIVE}--${REST}"
            fi
        # Unix absolute path (e.g., /Users/willow/Claude/Workshop)
        elif [ "${CLEAN:0:1}" = "/" ]; then
            local REST=$(echo "$CLEAN" | sed 's|^/||' | sed 's|/|-|g' | sed 's|\.|-|g')
            if [ -n "$REST" ]; then
                PROJECT_KEY="-${REST}"
            fi
        fi
    fi

    # Validate: check if the project directory exists
    if [ -n "$PROJECT_KEY" ] && [ ! -d "$CLAUDE_DIR/projects/$PROJECT_KEY" ]; then
        for DIR in "$CLAUDE_DIR/projects"/*/; do
            local DIRNAME=$(basename "$DIR")
            if echo "$PROJECT_KEY" | grep -qi "$(echo "$DIRNAME" | sed 's/--.*//'):"; then
                PROJECT_KEY="$DIRNAME"
                break
            fi
        done
    fi
}

# extract_session_uuid <stdin_json>
# Extracts session UUID from SessionStart hook stdin JSON payload.
# Tries session_id, sessionId, then CLAUDE_SESSION_ID env var.
# Sets global: SESSION_UUID
extract_session_uuid() {
    local INPUT="$1"
    SESSION_UUID=$(echo "$INPUT" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' 2>/dev/null)
    if [ -z "$SESSION_UUID" ]; then
        SESSION_UUID=$(echo "$INPUT" | sed -n 's/.*"sessionId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' 2>/dev/null)
    fi
    if [ -z "$SESSION_UUID" ]; then
        SESSION_UUID="$CLAUDE_SESSION_ID"
    fi
}

# lookup_partner <project_key> <session_uuid>
# Resolves session UUID to partner identity and chapter-specific routing.
#
# Reads partner-map.json structure:
#   {
#     "PROJECT_KEY": {
#       "partners": ["sage", "ember"],
#       "sessions": {
#         "UUID": {"partner": "ember", "session_log": "...", "inbox": "...", "session_prefix": "..."}
#       }
#     }
#   }
#
# Sets globals:
#   PARTNER             — partner identity (e.g. "ember", "sage")
#   PARTNERS            — comma-separated list of partners valid for this project
#                         (used in unregistered case to render registration prompt)
#   SESSION_LOG         — session-log filename (e.g. "bridgeember-session-log.md")
#   INBOX_NAME          — inbox name (e.g. "bridgeember"), used to build inbox path
#   SESSION_PREFIX      — prefix for session-status records (e.g. "bridgeember")
#   REGISTRATION_STATUS — "registered" | "unregistered" | "no-map"
#   PROJECT_PARTNERS    — same as PARTNERS, kept for clarity in unregistered case
lookup_partner() {
    local KEY="$1"
    local UUID="$2"
    PARTNER=""
    PARTNERS=""
    SESSION_LOG=""
    INBOX_NAME=""
    SESSION_PREFIX=""
    REGISTRATION_STATUS="no-map"
    PROJECT_PARTNERS=""

    if [ -z "$KEY" ] || [ ! -f "$PARTNER_MAP" ]; then
        return 0
    fi

    # Extract the project's partners list (always available from project entry)
    # We use python because nested JSON parsing in pure sed is error-prone.
    if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
        local PY
        if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi

        # Convert mingw/cygwin path to native Windows path if needed
        # (Python on Windows can't read /c/... paths; needs C:/...)
        local PM_PATH="$PARTNER_MAP"
        if command -v cygpath >/dev/null 2>&1; then
            PM_PATH=$(cygpath -w "$PARTNER_MAP" 2>/dev/null || echo "$PARTNER_MAP")
        fi

        # Single python invocation: extract partners list AND session entry (if registered)
        # Uses argv to avoid quote/escape issues with paths containing spaces or special chars.
        local RESULT=$("$PY" - "$PM_PATH" "$KEY" "$UUID" <<'PYEOF' 2>/dev/null
import json, sys
pm_path, key, uuid = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    m = json.load(open(pm_path))
except Exception:
    sys.exit(0)
proj = m.get(key)
if proj is None:
    sys.exit(0)
if isinstance(proj, str):
    # Legacy: bare string -> single partner
    print('PARTNERS=' + proj)
    print('STATUS=no-sessions')
    sys.exit(0)
if isinstance(proj, list):
    # Legacy: array -> multi-partner
    print('PARTNERS=' + ','.join(proj))
    print('STATUS=no-sessions')
    sys.exit(0)
# New schema: object with partners + sessions
partners = proj.get('partners', [])
print('PARTNERS=' + ','.join(partners))
sessions = proj.get('sessions', {})
entry = sessions.get(uuid)
if entry is None:
    print('STATUS=unregistered')
    sys.exit(0)
print('STATUS=registered')
print('PARTNER=' + entry.get('partner', ''))
print('SESSION_LOG=' + entry.get('session_log', ''))
print('INBOX_NAME=' + entry.get('inbox', ''))
print('SESSION_PREFIX=' + entry.get('session_prefix', ''))
PYEOF
)
        # Strip Windows CRLF line endings from python output (\r contaminates values)
        RESULT=$(echo "$RESULT" | tr -d '\r')

        # Parse result lines
        while IFS= read -r line; do
            case "$line" in
                PARTNERS=*) PARTNERS="${line#PARTNERS=}"; PROJECT_PARTNERS="$PARTNERS" ;;
                STATUS=*) REGISTRATION_STATUS="${line#STATUS=}" ;;
                PARTNER=*) PARTNER="${line#PARTNER=}" ;;
                SESSION_LOG=*) SESSION_LOG="${line#SESSION_LOG=}" ;;
                INBOX_NAME=*) INBOX_NAME="${line#INBOX_NAME=}" ;;
                SESSION_PREFIX=*) SESSION_PREFIX="${line#SESSION_PREFIX=}" ;;
            esac
        done <<< "$RESULT"
    fi

    # Apply defaults for missing fields when registered
    if [ "$REGISTRATION_STATUS" = "registered" ] && [ -n "$PARTNER" ]; then
        [ -z "$SESSION_LOG" ] && SESSION_LOG="${PARTNER}-session-log.md"
        [ -z "$INBOX_NAME" ] && INBOX_NAME="$PARTNER"
        [ -z "$SESSION_PREFIX" ] && SESSION_PREFIX="$PARTNER"
    fi

    # Legacy backward-compat: if STATUS=no-sessions (old schema), treat first
    # listed partner as the routing partner with all defaults. This keeps single-
    # partner projects working without a sessions registry.
    if [ "$REGISTRATION_STATUS" = "no-sessions" ] && [ -n "$PARTNERS" ]; then
        PARTNER=$(echo "$PARTNERS" | cut -d',' -f1)
        SESSION_LOG="${PARTNER}-session-log.md"
        INBOX_NAME="$PARTNER"
        SESSION_PREFIX="$PARTNER"
        REGISTRATION_STATUS="registered"
    fi
}

# detect_session_number <project_key> <session_prefix>
# Finds the most recent session-status record matching {prefix}-N_DATE.md.
# Sorts by parsed session number (not mtime) — fixes stale-file bug.
#
# The prefix IS the contract. If $PREFIX is "ember", looks for ember-N_*.md.
# If "bridgeember", looks for bridgeember-N_*.md. If "session" (Sage's
# override), looks for session-N_*.md. No cross-partner fallback — a chapter
# with no records simply returns empty (correct: there are no past sessions
# for that chapter yet).
#
# Sets globals: SESSION_NUM, SESSION_DATE, LATEST_SESSION_FILE
detect_session_number() {
    local KEY="$1"
    local PREFIX="$2"
    SESSION_NUM=""
    SESSION_DATE=""
    LATEST_SESSION_FILE=""

    [ -z "$PREFIX" ] && return 0

    local SESSION_STATUS_DIR="$CLAUDE_DIR/projects/$KEY/memory/session-status"
    [ ! -d "$SESSION_STATUS_DIR" ] && return 0

    local BEST_NUM=-1
    local BEST_FILE=""
    local BEST_DATE=""

    # Look for {PREFIX}-N_DATE.md only. The prefix IS the contract.
    for f in "$SESSION_STATUS_DIR/${PREFIX}-"*"_"*.md; do
        [ -f "$f" ] || continue
        local BASENAME=$(basename "$f" .md)
        local N=$(echo "$BASENAME" | sed -n "s/^${PREFIX}-\([0-9]*\)_.*/\1/p")
        local D=$(echo "$BASENAME" | sed -n "s/^${PREFIX}-[0-9]*_\(.*\)/\1/p")
        if [ -n "$N" ] && [ "$N" -gt "$BEST_NUM" ]; then
            BEST_NUM="$N"
            BEST_FILE="$f"
            BEST_DATE="$D"
        fi
    done

    if [ "$BEST_NUM" -ge 0 ]; then
        SESSION_NUM="$BEST_NUM"
        SESSION_DATE="$BEST_DATE"
        LATEST_SESSION_FILE="$BEST_FILE"
    fi
}

# extract_project_dir <stdin_json>
# Extracts project directory from hook stdin JSON payload.
# Tries projectDir, then cwd, then CLAUDE_PROJECT_DIR env var.
# Sets global: PROJECT_DIR
extract_project_dir() {
    local INPUT="$1"
    PROJECT_DIR=$(echo "$INPUT" | sed -n 's/.*"projectDir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' 2>/dev/null)
    if [ -z "$PROJECT_DIR" ]; then
        PROJECT_DIR=$(echo "$INPUT" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' 2>/dev/null)
    fi
    if [ -z "$PROJECT_DIR" ]; then
        PROJECT_DIR="$CLAUDE_PROJECT_DIR"
    fi
}

# json_escape <string>
# Escapes a string for safe JSON embedding (returns the inner content,
# without the surrounding double-quotes — caller wraps in quotes).
#
# Uses python3 when available (cross-platform, handles all edge cases).
# Falls back to BSD-compatible sed using separate -e expressions
# (the `:a;N;$!ba;s/...` semicolon-label form fails silently on BSD sed,
# which is why this function used to break on macOS).
json_escape() {
    if command -v python3 >/dev/null 2>&1; then
        printf '%s' "$1" | python3 -c 'import sys,json; sys.stdout.write(json.dumps(sys.stdin.read())[1:-1])'
    else
        printf '%s' "$1" | sed 's/\\/\\\\/g' | sed 's/"/\\"/g' | sed -e ':a' -e 'N' -e '$!ba' -e 's/\n/\\n/g'
    fi
}
