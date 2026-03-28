#!/bin/bash
# pretool-guard.sh — PreToolUse guard for Forge metadata and plugin directory protection
#
# Blocks writes to:
#   1. Protected forge/ input artifacts (scope.md, config.md)
#   2. Plugin directories (hooks/, agents/, skills/, scripts/, conventions/)
#      unless the build is self-modifying (Key Files section references plugin paths)
#
# Exit codes (PreToolUse convention):
#   0 = allow the operation
#   2 = block the operation
#
# Security:
#   SEC-001: Paths canonicalized before pattern matching
#   SEC-005: Self-modifying detection scoped to Key Files in config.md

set -uo pipefail

# --- Read stdin and extract file_path ---
INPUT=$(cat 2>/dev/null) || INPUT=""
if [ -z "$INPUT" ]; then
  echo '{"decision":"block","reason":"Blocked: malformed PreToolUse input (empty stdin)."}'
  exit 2
fi

if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || FILE_PATH=""
else
  FILE_PATH=$(echo "$INPUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))' 2>/dev/null) || FILE_PATH=""
fi

if [ -z "$FILE_PATH" ]; then
  echo '{"decision":"block","reason":"Blocked: could not extract file_path from PreToolUse input."}'
  exit 2
fi

# --- Path canonicalization (SEC-001) ---
# Normalize backslashes
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$FILE_PATH" | jq -Rr 'gsub("\\\\"; "/")')
else
  FILE_PATH=$(printf '%s' "$FILE_PATH" | tr '\\' '/')
fi

# Strip drive letter prefix
FILE_PATH="${FILE_PATH#[A-Za-z]:/}"
if [ -n "${MSYSTEM:-}" ]; then
  FILE_PATH="${FILE_PATH#/[A-Za-z]/}"
fi

# Strip project root to relative path
if [ -n "$FILE_PATH" ]; then
  PROJECT_ROOT="$(pwd)/"
  FILE_PATH="${FILE_PATH#"$PROJECT_ROOT"}"
  PROJECT_ROOT_NODRIVE="${PROJECT_ROOT#/[A-Za-z]/}"
  FILE_PATH="${FILE_PATH#"$PROJECT_ROOT_NODRIVE"}"
fi

# Remove leading ./ and internal /./
FILE_PATH="${FILE_PATH#./}"
FILE_PATH="${FILE_PATH//\/.\///}"

# Collapse ../ sequences
if [[ "$FILE_PATH" == *..* ]]; then
  IFS='/' read -ra PARTS <<< "$FILE_PATH"
  RESOLVED=()
  for part in "${PARTS[@]}"; do
    if [ "$part" = ".." ]; then
      [ ${#RESOLVED[@]} -gt 0 ] && unset 'RESOLVED[${#RESOLVED[@]}-1]'
    elif [ "$part" != "." ] && [ -n "$part" ]; then
      RESOLVED+=("$part")
    fi
  done
  FILE_PATH=$(IFS='/'; echo "${RESOLVED[*]}")
fi

# --- Quick exit: not a guarded path ---
case "$FILE_PATH" in
  forge/*|hooks/*|agents/*|skills/*|scripts/*|conventions/*)
    ;; # Continue to checks
  *)
    exit 0 ;;
esac

# --- Check if Forge build is active ---
if [ ! -f "forge/config.md" ]; then
  exit 0
fi

# --- Helper: emit block JSON safely (handles special chars in paths) ---
emit_block() {
  local reason="$1"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$reason" | python3 -c "import sys,json; print(json.dumps({'decision':'block','reason':sys.stdin.read()}))" 2>/dev/null \
      || echo '{"decision":"block","reason":"Blocked: write denied by pretool-guard."}'
  else
    echo '{"decision":"block","reason":"Blocked: write denied by pretool-guard."}'
  fi
}

# --- Protected forge input artifacts (only frozen during active build) ---
if [ -f "forge/.build-active" ] && [[ "$FILE_PATH" =~ ^forge/(scope|config)\.md$ ]]; then
  emit_block "Blocked: $FILE_PATH is frozen during build. Complete or cancel the build to edit."
  exit 2
fi

# --- Plugin directory protection (per-directory, not all-or-nothing) ---
case "$FILE_PATH" in
  hooks/*|agents/*|skills/*|scripts/*|conventions/*)
    FILE_DIR="${FILE_PATH%%/*}/"
    ALLOWED_DIRS=$(sed -n '/^## Key Files/,/^## /{/^## Key Files/d;/^## /d;p;}' "forge/config.md" 2>/dev/null \
      | grep -oE '^(hooks|agents|skills|scripts|conventions)/' \
      | sort -u)
    if [ -z "$ALLOWED_DIRS" ] || ! echo "$ALLOWED_DIRS" | grep -qF "$FILE_DIR"; then
      emit_block "Blocked: $FILE_PATH is in a protected plugin directory. Add its directory to Key Files in forge/config.md for self-modifying builds."
      exit 2
    fi
    ;;
esac

exit 0
