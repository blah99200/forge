#!/bin/bash
# review-dispatch.sh — Unified API dispatch for Forge multi-model review
#
# Replaces review-gemini.sh, review-codex.sh, and review-grok.sh with a single
# script that dispatches to any OpenAI-compatible Chat Completions endpoint.
#
# Usage: review-dispatch.sh <provider> <endpoint> <key-env-var> <model> <payload-file> [validation-mode] [thinking-level]
#
#   provider        — Provider name for telemetry/logging (gemini, openai, grok)
#   endpoint        — Full Chat Completions URL
#   key-env-var     — Name of the environment variable holding the API key (e.g., GEMINI_API_KEY)
#   model           — Model ID to use (e.g., gemini-3-flash-preview, gpt-5.4-mini)
#   payload-file    — Path to the prompt file (contents become the user message)
#   validation-mode — Optional: "review" (default), "tribunal", or "scope-validation"
#   thinking-level  — Optional: "none", "low", "medium", "high" (omitted for grok)
#
# Exit codes:
#   0 — Success (stdout: "Review-Model: <model>" header + clean content)
#   1 — Fatal error (missing key, missing file, non-transient HTTP error)
#   2 — Exhausted after retries (caller should degrade gracefully)

set -euo pipefail

# Source shared dispatch library
LIB_DIR="$(dirname "$0")"
if [ -f "$LIB_DIR/model-dispatch-lib.sh" ]; then
  source "$LIB_DIR/model-dispatch-lib.sh"
else
  echo "Error: model-dispatch-lib.sh not found at $LIB_DIR. Stage both scripts together." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

PROVIDER="${1:?Usage: review-dispatch.sh <provider> <endpoint> <key-env-var> <model> <payload-file> [validation-mode] [thinking-level]}"
ENDPOINT="${2:?Missing endpoint URL}"
KEY_ENV_VAR="${3:?Missing API key env var name}"
MODEL="${4:?Missing model ID}"
PAYLOAD_FILE="${5:?Missing payload file path}"
VALIDATION_MODE="${6:-review}"
THINKING_LEVEL="${7:-}"

# ---------------------------------------------------------------------------
# Dispatch telemetry setup
# ---------------------------------------------------------------------------

DISPATCH_START_MS=$(date +%s%3N 2>/dev/null || echo "0")
_TELEMETRY_MODEL="$MODEL"
_TELEMETRY_OUTCOME=""
_TELEMETRY_ATTEMPTS=0

trap 'emit_dispatch_telemetry' EXIT

# ---------------------------------------------------------------------------
# Retry config
# ---------------------------------------------------------------------------

BACKOFF=(30 60 120)
MAX_RETRIES=3

# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

API_KEY="${!KEY_ENV_VAR:-}"

if [ -z "$API_KEY" ]; then
  for _profile in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc" "$HOME/.profile"; do
    if [ -f "$_profile" ]; then
      _extracted=$(sed -n "s/^export ${KEY_ENV_VAR}=\([^ ]*\).*/\1/p" "$_profile" 2>/dev/null | tail -1)
      # Strip surrounding quotes if present
      _extracted="${_extracted%\"}"
      _extracted="${_extracted#\"}"
      _extracted="${_extracted%\'}"
      _extracted="${_extracted#\'}"
      if [ -n "$_extracted" ]; then
        API_KEY="$_extracted"
        echo "API key sourced from $_profile" >&2
        break
      fi
    fi
  done
  unset _profile _extracted
fi

if [ -z "$API_KEY" ]; then
  _TELEMETRY_OUTCOME="error"
  echo "Error: ${KEY_ENV_VAR} is not set or empty. Set it to your ${PROVIDER} API key." >&2
  exit "$EXIT_FATAL"
fi

# ---------------------------------------------------------------------------
# Validate payload file
# ---------------------------------------------------------------------------

if [ ! -f "$PAYLOAD_FILE" ]; then
  _TELEMETRY_OUTCOME="error"
  echo "Error: Payload file not found: $PAYLOAD_FILE" >&2
  exit "$EXIT_FATAL"
fi

# ---------------------------------------------------------------------------
# Auto-redact payload before external dispatch (SEC-002)
# ---------------------------------------------------------------------------

if type redact_payload &>/dev/null; then
  REDACTED_FILE=$(redact_payload "$PAYLOAD_FILE")
  PROMPT_CONTENT="$(cat "$REDACTED_FILE")"
  rm -f "$REDACTED_FILE"
else
  PROMPT_CONTENT="$(cat "$PAYLOAD_FILE")"
fi

# ---------------------------------------------------------------------------
# Code fence stripping — strips outermost ``` or ~~~ delimiters
# ---------------------------------------------------------------------------

strip_outer_code_fences() {
  local input="$1"
  local first_line last_line

  first_line="$(echo "$input" | head -1)"
  last_line="$(echo "$input" | tail -1)"

  # Guard: if input is a single line, don't strip (would produce empty output)
  local line_count
  line_count=$(echo "$input" | wc -l)
  if [ "$line_count" -le 1 ]; then
    echo "$input"
    return
  fi

  if { [[ "$first_line" =~ ^'```' ]] && [[ "$last_line" =~ ^'```' ]]; } ||
     { [[ "$first_line" =~ ^'~~~' ]] && [[ "$last_line" =~ ^'~~~' ]]; }; then
    echo "$input" | tail -n +2 | sed '$d'
  else
    echo "$input"
  fi
}

# ---------------------------------------------------------------------------
# Build JSON payload
# ---------------------------------------------------------------------------

build_payload() {
  local model="$1"
  local thinking="$2"

  if [ -n "$thinking" ] && [ "$PROVIDER" != "grok" ]; then
    # Include reasoning_effort for OpenAI and Gemini
    printf '%s' "$PROMPT_CONTENT" | jq -Rs \
      --arg model "$model" \
      --arg effort "$thinking" \
      '{
        model: $model,
        reasoning_effort: $effort,
        messages: [{ role: "user", content: . }]
      }'
  else
    # No reasoning_effort (Grok or no thinking level specified)
    printf '%s' "$PROMPT_CONTENT" | jq -Rs \
      --arg model "$model" \
      '{
        model: $model,
        messages: [{ role: "user", content: . }]
      }'
  fi
}

# ---------------------------------------------------------------------------
# Dispatch loop
# ---------------------------------------------------------------------------

REASONING_REJECTED=false

for (( retry=0; retry<MAX_RETRIES; retry++ )); do
  ATTEMPT=$((retry + 1))
  _TELEMETRY_ATTEMPTS=$ATTEMPT

  if [ "$ATTEMPT" -gt 1 ]; then
    echo "Attempt $ATTEMPT/$MAX_RETRIES (${MODEL})..." >&2
  fi

  # Build JSON payload (SEC-002: never string-interpolate prompt content)
  if [ "$REASONING_REJECTED" = true ]; then
    JSON_PAYLOAD=$(build_payload "$MODEL" "")
  else
    JSON_PAYLOAD=$(build_payload "$MODEL" "$THINKING_LEVEL")
  fi

  # Dispatch via curl (SEC-001: API key only via -H header)
  set +e
  HTTP_RESPONSE=$(printf '%s' "$JSON_PAYLOAD" | curl -s -w '\n%{http_code}' \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $API_KEY" \
    -d @- \
    "$ENDPOINT" 2>/dev/null)
  CURL_EXIT=$?
  set -e

  # Split response body and HTTP status code
  HTTP_CODE=$(echo "$HTTP_RESPONSE" | tail -1)
  RESPONSE=$(echo "$HTTP_RESPONSE" | sed '$d')

  # --- Transport failure (curl exit codes) ---
  if [ "$CURL_EXIT" -ne 0 ]; then
    echo "${PROVIDER}: curl transport failure (exit $CURL_EXIT) on attempt $ATTEMPT." >&2
    if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
      sleep_with_jitter "${BACKOFF[$retry]}"
      continue
    fi
    break
  fi

  # --- Validate JSON response ---
  if ! echo "$RESPONSE" | jq empty 2>/dev/null; then
    echo "${PROVIDER}: invalid JSON response on attempt $ATTEMPT." >&2
    if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
      sleep_with_jitter "${BACKOFF[$retry]}"
      continue
    fi
    break
  fi

  # --- HTTP error routing ---
  case "$HTTP_CODE" in
    200)
      # Check for error field in 200 response (some providers do this)
      ERROR_MSG=$(echo "$RESPONSE" | jq -r '.error.message // empty' 2>/dev/null)
      if [ -n "$ERROR_MSG" ]; then
        echo "${PROVIDER}: API error in 200 response: $ERROR_MSG" >&2
        if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
          sleep_with_jitter "${BACKOFF[$retry]}"
          continue
        fi
        break
      fi
      ;;
    400)
      ERROR_MSG=$(echo "$RESPONSE" | jq -r '.error.message // empty' 2>/dev/null)
      # Graceful degradation: if reasoning_effort was rejected, retry without it
      if [ -n "$THINKING_LEVEL" ] && [ "$REASONING_REJECTED" = false ]; then
        if echo "$ERROR_MSG" | grep -qiE 'reasoning|effort|unsupported.*param'; then
          echo "${PROVIDER}: reasoning_effort rejected, retrying without it." >&2
          REASONING_REJECTED=true
          # Don't count this as a retry attempt
          retry=$((retry - 1))
          continue
        fi
      fi
      _TELEMETRY_OUTCOME="error"
      echo "${PROVIDER}: fatal HTTP 400 — ${ERROR_MSG:-bad request}. Not retrying." >&2
      exit "$EXIT_FATAL"
      ;;
    401|403)
      ERROR_MSG=$(echo "$RESPONSE" | jq -r '.error.message // empty' 2>/dev/null)
      _TELEMETRY_OUTCOME="error"
      echo "${PROVIDER}: fatal HTTP $HTTP_CODE — ${ERROR_MSG:-auth error}. Not retrying." >&2
      exit "$EXIT_FATAL"
      ;;
    404|408|409|422)
      ERROR_MSG=$(echo "$RESPONSE" | jq -r '.error.message // empty' 2>/dev/null)
      _TELEMETRY_OUTCOME="error"
      echo "${PROVIDER}: fatal HTTP $HTTP_CODE — ${ERROR_MSG:-client error}. Not retrying." >&2
      exit "$EXIT_FATAL"
      ;;
    429|5[0-9][0-9])
      ERROR_MSG=$(echo "$RESPONSE" | jq -r '.error.message // empty' 2>/dev/null)
      echo "${PROVIDER}: transient HTTP $HTTP_CODE: ${ERROR_MSG:-rate limited or server error}" >&2
      if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
        sleep_with_jitter "${BACKOFF[$retry]}"
        continue
      fi
      break
      ;;
    *)
      echo "${PROVIDER}: unexpected HTTP $HTTP_CODE on attempt $ATTEMPT." >&2
      if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
        sleep_with_jitter "${BACKOFF[$retry]}"
        continue
      fi
      break
      ;;
  esac

  # --- Extract content ---
  CONTENT=$(echo "$RESPONSE" | jq -r '.choices[0].message.content // empty')

  if [ -z "$CONTENT" ]; then
    echo "${PROVIDER}: empty content in response on attempt $ATTEMPT." >&2
    if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
      sleep_with_jitter "${BACKOFF[$retry]}"
      continue
    fi
    break
  fi

  # --- Strip outer code fences ---
  CONTENT=$(strip_outer_code_fences "$CONTENT")

  if [ -z "$CONTENT" ]; then
    echo "${PROVIDER}: content empty after fence stripping on attempt $ATTEMPT." >&2
    if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
      sleep_with_jitter "${BACKOFF[$retry]}"
      continue
    fi
    break
  fi

  # --- Validate output structure ---
  if type validate_model_output &>/dev/null; then
    if ! validate_model_output "$CONTENT" "$MODEL" "$VALIDATION_MODE"; then
      echo "${PROVIDER}: structural validation failed on attempt $ATTEMPT." >&2
      if [ "$retry" -lt $((MAX_RETRIES - 1)) ]; then
        sleep_with_jitter "${BACKOFF[$retry]}"
        continue
      fi
      break
    fi
  fi

  # --- Success: output header + content (nothing to stdout before this point) ---
  echo "Review-Model: $MODEL"
  echo "$CONTENT"
  echo "${PROVIDER}/${MODEL} succeeded on attempt $ATTEMPT." >&2
  _TELEMETRY_OUTCOME="success"
  exit "$EXIT_SUCCESS"
done

# All retries exhausted
_TELEMETRY_OUTCOME="exhausted"
echo "${PROVIDER}: ${MODEL} exhausted after $MAX_RETRIES attempts." >&2
exit "$EXIT_EXHAUSTED"
