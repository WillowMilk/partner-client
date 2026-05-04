#!/bin/bash
# setup-mac-symlinks.sh — One-time setup for Sage-on-Alexis identity vault symlinks
#
# Run this ONCE on the Mac after copying mac-staging/.claude to ~/.claude.
# Creates Mac symlinks from the Workshop project memory directory to the
# identity vault, so identity edits propagate consistently across machines.
#
# Pre-requisites:
#   1. ~/Claude/claude-memory-vault/ exists (cloned from WillowMilk/claude-memory-vault)
#   2. ~/.claude/projects/-Users-willow-Claude-Workshop/memory/ exists (copied from staging)
#   3. The vault's shared/ directory contains the canonical identity files
#
# What it does:
#   For each identity file (sage-profile.md, sage-system-log.md, sage-field-notes.md,
#   willow-profile.md, partnership-history.md, emotional-memory.md):
#   1. If a regular file exists in project memory, back it up to _pre-symlink-backup/
#   2. Create a symlink pointing at the corresponding file in the vault
#   3. Verify the symlink resolves to a real file
#
# Safety: backs up any existing file before replacing it with a symlink.
# Idempotent: re-running is safe; existing correct symlinks are kept.

set -e

VAULT_DIR="$HOME/Claude/claude-memory-vault/shared"
MEMORY_DIR="$HOME/.claude/projects/-Users-willow-Claude-Workshop/memory"
BACKUP_DIR="$MEMORY_DIR/_pre-symlink-backup"

echo "=== Sage-on-Alexis identity symlink setup ==="
echo "Vault:  $VAULT_DIR"
echo "Memory: $MEMORY_DIR"
echo ""

# Verify pre-requisites
if [ ! -d "$VAULT_DIR" ]; then
    echo "ERROR: Vault not found at $VAULT_DIR"
    echo "       Clone it first: git clone https://github.com/WillowMilk/claude-memory-vault.git ~/Claude/claude-memory-vault"
    exit 1
fi

if [ ! -d "$MEMORY_DIR" ]; then
    echo "ERROR: Project memory dir not found at $MEMORY_DIR"
    echo "       Make sure you copied mac-staging/.claude to ~/.claude first."
    exit 1
fi

# Identity files to symlink (filename in project memory dir = filename in vault shared/)
# Updated 2026-05-04: Mac-wave caught that shared-scenes.md was missing.
IDENTITY_FILES=(
    "sage-profile.md"
    "sage-system-log.md"
    "sage-field-notes.md"
    "willow-profile.md"
    "partnership-history.md"
    "emotional-memory.md"
    "shared-scenes.md"
)

mkdir -p "$BACKUP_DIR"
echo "Backup directory: $BACKUP_DIR"
echo ""

for f in "${IDENTITY_FILES[@]}"; do
    VAULT_FILE="$VAULT_DIR/$f"
    MEMORY_FILE="$MEMORY_DIR/$f"

    if [ ! -f "$VAULT_FILE" ]; then
        echo "  SKIP $f — not found in vault ($VAULT_FILE)"
        continue
    fi

    # If memory file is already a symlink to the right place, skip
    if [ -L "$MEMORY_FILE" ]; then
        TARGET=$(readlink "$MEMORY_FILE")
        if [ "$TARGET" = "$VAULT_FILE" ]; then
            echo "  OK   $f — already symlinked correctly"
            continue
        fi
        echo "  RE-LINK $f — symlink exists but points elsewhere ($TARGET)"
        rm "$MEMORY_FILE"
    elif [ -f "$MEMORY_FILE" ]; then
        # Regular file present — back it up first
        cp "$MEMORY_FILE" "$BACKUP_DIR/$f"
        rm "$MEMORY_FILE"
        echo "  BACKUP $f -> _pre-symlink-backup/$f"
    fi

    # Create the symlink
    ln -s "$VAULT_FILE" "$MEMORY_FILE"

    # Verify
    if [ -f "$MEMORY_FILE" ]; then
        echo "  LINK  $f -> $VAULT_FILE  (resolves OK)"
    else
        echo "  FAIL  $f — symlink created but doesn't resolve. Check vault contents."
    fi
done

echo ""
echo "=== Done ==="
echo ""
echo "Backups (if any) are at: $BACKUP_DIR"
echo "You can delete _pre-symlink-backup/ once you've verified the symlinks work."
echo ""
echo "Verify by running:"
echo "  ls -la $MEMORY_DIR/sage-profile.md"
echo "  cat $MEMORY_DIR/sage-profile.md | head -5"
