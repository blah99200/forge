#!/bin/bash
# model-dispatch-lib.sh — Shared functions for Forge review dispatch
#
# Sourceable library providing common functions for review-dispatch.sh.
# Single source of truth for dispatch telemetry, retry helpers, error
# classification, validation, and payload redaction.
#
# Usage:
#   source "$(dirname "$0")/model-dispatch-lib.sh"
#
# Exported functions:
#   emit_dispatch_telemetry — Emit JSON telemetry event to stderr (call via trap on EXIT)
#   sleep_with_jitter <base_delay> [max_jitter] — Sleep with random jitter for retry backoff
#   is_transient_error <stderr_file> — Check if stderr contains transient error patterns
#   validate_model_output <output> <model_name> [mode] — Validate structural integrity of model response
#   redact_payload <payload_file> — Create a redacted copy of a payload file, return path to copy
#   forge_default_model <provider> <tier> — Resolve default model ID for a provider/tier
#   forge_default_thinking <provider> <tier> — Resolve default thinking level for a provider/tier
#   forge_context_window <provider> [tier] — Get context window size (tokens) for a provider/tier
#
# Telemetry protocol:
#   Before sourcing, the caller MUST set DISPATCH_START_MS=$(date +%s%3N || echo 0).
#   The caller sets these variables before exit to control telemetry output:
#     _TELEMETRY_MODEL    — model name (e.g., "gemini-2.5-pro", "gpt-5.4-mini")
#     _TELEMETRY_OUTCOME  — "success", "error", "exhausted", "timeout"
#     _TELEMETRY_ATTEMPTS — number of attempts made (integer)
#   Optional fallback fields (Gemini only):
#     _TELEMETRY_FALLBACK_MODEL    — fallback model name (empty if no fallback)
#     _TELEMETRY_FALLBACK_OUTCOME  — fallback outcome
#     _TELEMETRY_FALLBACK_ATTEMPTS — fallback attempts
#
# Exit code constants:
#   EXIT_SUCCESS=0  — Review succeeded
#   EXIT_FATAL=1    — Fatal error (missing API key, bad arguments)
#   EXIT_EXHAUSTED=2 — Model unavailable after retries (caller should fall back)

# Exit code constants
EXIT_SUCCESS=0
EXIT_FATAL=1
EXIT_EXHAUSTED=2

# ---------------------------------------------------------------------------
# Model defaults — loaded from models.json (data separate from logic)
# ---------------------------------------------------------------------------
# To update models: edit scripts/models.json, not this file.
# Per-initiative overrides via config.md Models section take precedence.

_FORGE_MODELS_FILE="$(dirname "${BASH_SOURCE[0]}")/models.json"

# Load a field from models.json: _model_field <provider> <tier> <field>
_model_field() {
  local provider="$1" tier="$2" field="$3"
  if [ -f "$_FORGE_MODELS_FILE" ] && command -v jq >/dev/null 2>&1; then
    jq -r --arg p "$provider" --arg t "$tier" --arg f "$field" \
      '.[$p][$t][$f] // empty' "$_FORGE_MODELS_FILE" 2>/dev/null
  elif [ -f "$_FORGE_MODELS_FILE" ] && command -v python3 >/dev/null 2>&1; then
    python3 -c "
import json, sys
with open('$_FORGE_MODELS_FILE') as f: d = json.load(f)
v = d.get('$provider', {}).get('$tier', {}).get('$field', '')
print(v if v else '')
" 2>/dev/null
  fi
}

# Resolve default model for a provider/tier combination.
# Usage: model=$(forge_default_model gemini deep)
forge_default_model() {
  local provider="${1:?Usage: forge_default_model <provider> <tier>}"
  local tier="${2:?Usage: forge_default_model <provider> <tier>}"
  _model_field "$provider" "$tier" "model"
}

# Resolve default thinking level for a provider/tier combination.
# Usage: thinking=$(forge_default_thinking gemini deep)
forge_default_thinking() {
  local provider="${1:?Usage: forge_default_thinking <provider> <tier>}"
  local tier="${2:?Usage: forge_default_thinking <provider> <tier>}"
  _model_field "$provider" "$tier" "thinking"
}

# Get context window size for a provider/tier (tokens). Falls back to 1M.
# Usage: window=$(forge_context_window gemini deep)
forge_context_window() {
  local provider="${1:?Usage: forge_context_window <provider> [tier]}"
  local tier="${2:-deep}"
  local window
  window=$(_model_field "$provider" "$tier" "context")
  echo "${window:-1000000}"
}

# Emit dispatch outcome JSON to stderr. Call via trap on EXIT.
# All _TELEMETRY_* variables must be set by the caller before exit.
# Model names are caller-controlled hardcoded strings — no injection vector (SEC-003).
emit_dispatch_telemetry() {
  local end_ms
  end_ms=$(date +%s%3N 2>/dev/null || echo "0")
  local duration_ms=$(( end_ms - DISPATCH_START_MS ))
  local timestamp
  timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "1970-01-01T00:00:00Z")

  local json="{\"event\":\"model_dispatch\",\"model\":\"${_TELEMETRY_MODEL}\",\"outcome\":\"${_TELEMETRY_OUTCOME}\",\"attempts\":${_TELEMETRY_ATTEMPTS:-0},\"duration_ms\":${duration_ms},\"timestamp\":\"${timestamp}\""

  # Add fallback fields if fallback was used (Gemini model chain)
  if [[ -n "${_TELEMETRY_FALLBACK_MODEL:-}" ]]; then
    json="${json},\"fallback_model\":\"${_TELEMETRY_FALLBACK_MODEL}\",\"fallback_outcome\":\"${_TELEMETRY_FALLBACK_OUTCOME}\",\"fallback_attempts\":${_TELEMETRY_FALLBACK_ATTEMPTS}"
  fi

  json="${json}}"
  echo "$json" >&2
}

# Sleep with random jitter for retry backoff. Prevents thundering herd.
# Usage: sleep_with_jitter <base_delay> [max_jitter]
#   base_delay: base seconds to sleep
#   max_jitter: maximum random jitter in seconds (default: 30)
sleep_with_jitter() {
  local base_delay="${1:?Usage: sleep_with_jitter <base_delay> [max_jitter]}"
  local max_jitter="${2:-30}"
  local jitter=$(( RANDOM % max_jitter ))
  local total=$(( base_delay + jitter ))
  echo "Retrying after ${total}s (${base_delay}s + ${jitter}s jitter)..." >&2
  sleep "$total"
}

# Validate structural integrity of model output.
# Checks minimum length, verdict presence, and section headers.
# Returns 0 if valid, 1 if invalid. Failure reasons printed to stderr.
# Relaxed fallback: when verdict + length pass but headers fail, returns 0
# with a warning on stderr and a [RELAXED-VALIDATION] marker on stdout.
#
# Modes:
#   review (default) — expects APPROVE/REVISE/REWORK or CLEAN/ADVISORY/BLOCKED verdicts
#   tribunal         — expects VALIDATE/DOWNGRADE/REJECT verdicts
#
# Usage: validate_model_output "$OUTPUT" "gemini" [mode]
validate_model_output() {
  local output="${1:?Usage: validate_model_output <output> <model_name> [mode]}"
  local model_name="${2:?Usage: validate_model_output <output> <model_name> [mode]}"
  local mode="${3:-review}"
  local failures=()

  # Track individual check results for relaxed fallback path
  local length_ok=true
  local verdict_ok=true
  local headers_ok=true

  # Check 1: Minimum length (100 chars) — a real response is never shorter
  local length=${#output}
  if [ "$length" -lt 100 ]; then
    failures+=("minimum length: got ${length} chars, need at least 100")
    length_ok=false
  fi

  # Check 2: Verdict presence (case-insensitive) — expected verdicts depend on mode
  # Handles variations like "Verdict: APPROVE", "**APPROVE**", "verdict: approve"
  if [ "$mode" = "tribunal" ]; then
    if ! echo "$output" | grep -qiE '\b(VALIDATE|DOWNGRADE|REJECT)\b'; then
      failures+=("verdict missing: response must contain VALIDATE, DOWNGRADE, or REJECT")
      verdict_ok=false
    fi
  else
    # Review mode: reviewer verdicts or scope-validator verdicts
    if ! echo "$output" | grep -qiE '\b(APPROVE|REVISE|REWORK|CLEAN|ADVISORY|BLOCKED)\b'; then
      failures+=("verdict missing: response must contain APPROVE, REVISE, REWORK, CLEAN, ADVISORY, or BLOCKED")
      verdict_ok=false
    fi
  fi

  # Check 3: Section headers — at least 2 markdown headers (## or ###)
  # Skipped for tribunal mode: tribunal responses are short debate arguments
  # that naturally lack section headers. Verdict + minimum length is sufficient.
  if [ "$mode" != "tribunal" ]; then
    local header_count
    header_count=$(echo "$output" | grep -cE '^#{2,3} ' || true)
    if [ "$header_count" -lt 2 ]; then
      failures+=("section headers: found ${header_count}, need at least 2 (## or ###)")
      headers_ok=false
    fi
  fi

  # Primary path: all checks passed — accept
  if [ ${#failures[@]} -eq 0 ]; then
    return 0
  fi

  # Relaxed fallback path: verdict + length passed but headers failed.
  # Accept with a warning instead of failing. This prevents valid model
  # responses (substantive content with verdict) from being rejected solely
  # due to missing section headers.
  # SEC-003: Only activates when verdict AND length both pass but headers fail.
  # Uses explicit if/elif/else — no complex &&/|| chains.
  if [ "$length_ok" = true ]; then
    if [ "$verdict_ok" = true ]; then
      if [ "$headers_ok" = false ]; then
        echo "WARNING: ${model_name}: accepted without section headers (verdict + length OK, headers relaxed)" >&2
        # REQ-010: Marker to stderr only — stdout reserved for Review-Model header + content
        echo "[RELAXED-VALIDATION] ${model_name}: response accepted without section headers (verdict + length OK)" >&2
        return 0
      fi
    fi
  fi

  # Hard failure: verdict or length failed
  echo "Validation failed for ${model_name} response:" >&2
  for f in "${failures[@]}"; do
    echo "  - $f" >&2
  done
  return 1
}

# Redact high-entropy secrets from a payload file before external dispatch.
# Creates a transient redacted copy — the original file is never modified.
# Returns the path to the redacted copy via stdout.
#
# Scans for:
#   - API keys: sk-*, AIza*, xai-*, ghp_*, ghs_*, AKIA*
#   - Connection strings: ://user:pass@host
#   - Bearer tokens: Bearer <long-token>
#
# PII redaction (email, phone) is opt-in — pass "pii" as second argument.
#
# Usage: redacted_path=$(redact_payload "/path/to/payload.md" [pii])
redact_payload() {
  local payload_file="${1:?Usage: redact_payload <payload_file> [pii]}"
  local redact_pii="${2:-}"
  local redacted_file="${payload_file%.md}-redacted.md"

  # Copy original — never modify in place
  cp "$payload_file" "$redacted_file"

  # High-entropy secret patterns (always applied)
  # API keys (include hyphens/underscores for keys like sk-proj-xxx)
  sed -i -E 's/sk-[a-zA-Z0-9_-]{20,}/[REDACTED:api-key]/g' "$redacted_file"
  sed -i -E 's/AIza[0-9A-Za-z_-]{35}/[REDACTED:api-key]/g' "$redacted_file"
  sed -i -E 's/xai-[a-zA-Z0-9]{20,}/[REDACTED:api-key]/g' "$redacted_file"
  sed -i -E 's/ghp_[a-zA-Z0-9]{36}/[REDACTED:api-key]/g' "$redacted_file"
  sed -i -E 's/ghs_[a-zA-Z0-9]{36}/[REDACTED:api-key]/g' "$redacted_file"
  # AWS access key IDs
  sed -i -E 's/AKIA[0-9A-Z]{16}/[REDACTED:credential]/g' "$redacted_file"
  # Connection strings (://user:pass@host — greedy match handles @ in passwords)
  sed -i -E 's|://[^ ]*@|://[REDACTED:connection-string]@|g' "$redacted_file"
  # Bearer tokens (long values only, not the word "Bearer" in documentation)
  sed -i -E 's/Bearer [A-Za-z0-9._-]{20,}/Bearer [REDACTED:bearer-token]/g' "$redacted_file"

  # Optional PII redaction (email, phone)
  if [ "$redact_pii" = "pii" ]; then
    # Email addresses
    sed -i -E 's/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/[REDACTED:pii]/g' "$redacted_file"
    # Phone numbers (common formats)
    sed -i -E 's/\b[0-9]{3}[-. ][0-9]{3}[-. ][0-9]{4}\b/[REDACTED:pii]/g' "$redacted_file"
    sed -i -E 's/\+[0-9]{1,3}[-. ]?[0-9]{4,14}/[REDACTED:pii]/g' "$redacted_file"
  fi

  local redaction_count
  redaction_count=$(grep -c '\[REDACTED:' "$redacted_file" 2>/dev/null) || redaction_count=0
  if [ "$redaction_count" -gt 0 ]; then
    echo "Redacted $redaction_count sensitive patterns from payload." >&2
  fi

  echo "$redacted_file"
}
