#!/bin/bash
# post-edit-test-runner.sh — Auto-run tests after file edits during Forge TDD workflow
#
# Runs asynchronously after Write|Edit tool calls. Only triggers when:
# 1. forge/config.md exists (we're in a Forge-managed project)
# 2. The edited file is a source or test file (not forge/ metadata)
#
# Returns test results as a systemMessage so Claude sees failures immediately.

set -euo pipefail

INPUT=$(cat)

# Extract file_path using jq (proper JSON parser), with python3 fallback
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || FILE_PATH=""
else
  FILE_PATH=$(echo "$INPUT" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))' 2>/dev/null) || FILE_PATH=""
fi

# Skip if no file path
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Skip forge metadata files
if echo "$FILE_PATH" | grep -qE 'forge/(scope|config|review|issues|security|validation|handoff)\.md$' || echo "$FILE_PATH" | grep -qE 'forge/.*-(raw|payload|prompt)\.md$'; then
  exit 0
fi

# Skip config files
if echo "$FILE_PATH" | grep -qE '(CLAUDE\.md|\.json|\.yml|\.yaml|\.toml|\.ini|\.cfg)$'; then
  exit 0
fi

# Find config.md — v2 uses flat forge/config.md (no multi-initiative)
CONFIG_FILE="forge/config.md"
if [ ! -f "$CONFIG_FILE" ]; then
  exit 0
fi

# Extract test command from config.md
TEST_CMD=$(grep -i "test command" "$CONFIG_FILE" 2>/dev/null | sed 's/^[^:]*: *//' | head -1 || true)

if [ -z "$TEST_CMD" ]; then
  exit 0
fi

# Validate test command: block shell metacharacters that could enable injection
if echo "$TEST_CMD" | grep -qE '[;|&$`\\(){}<>!]'; then
  echo '{"systemMessage": "Skipped test run: test command contains disallowed shell characters."}'
  exit 0
fi

# Run tests
set +e
RESULT=$(bash -c "$TEST_CMD" 2>&1)
EXIT_CODE=$?
set -e

BASENAME=$(basename "$FILE_PATH")
if ! command -v python3 >/dev/null 2>&1; then
  # Fallback: simple JSON without escaping (best effort without python3)
  if [ "$EXIT_CODE" -eq 0 ]; then
    echo "{\"systemMessage\": \"Tests passing after editing $BASENAME\"}"
  else
    echo "{\"systemMessage\": \"Tests FAILING after editing $BASENAME (install python3 for full output)\"}"
  fi
  exit 0
fi

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "$BASENAME" | python3 -c '
import sys, json
name = sys.stdin.read().strip()
print(json.dumps({"systemMessage": f"Tests passing after editing {name}"}))
'
else
  TRUNCATED=$(echo "$RESULT" | tail -30)
  echo "$TRUNCATED" | python3 -c '
import sys, json
output = sys.stdin.read()
name = sys.argv[1]
print(json.dumps({"systemMessage": f"Tests FAILING after editing {name}:\n{output}"}))
' "$BASENAME"
fi
