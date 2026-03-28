# Forge

A Claude Code plugin for TDD-enforced development with cross-model review.

Forge coordinates 6 specialized agents through a strict build pipeline: scope requirements, write failing tests, implement, validate, and review with independent external models — all enforced by mechanical hooks.

## Install

```bash
# Add to your shell profile (~/.bashrc, ~/.zshrc, etc.)
alias claude='claude --plugin-dir /path/to/forge'
```

## Usage

```bash
# 1. Scope your work
/forge:scope "add user authentication with JWT tokens"

# 2. Build it (runs the full TDD pipeline)
/forge:build

# 3. Or run standalone review/validation on existing code
/forge:review
/forge:validate
```

## How It Works

### Pipeline

```
/forge:scope     User describes what to build. Scoper gathers requirements,
                 explores codebase, writes forge/scope.md + forge/config.md.

/forge:build     Orchestrates the full cycle per phase:

                 SECURITY    security-reviewer analyzes scope, writes forge/security.md
                 RED         test-architect writes failing tests from requirements
                 GREEN       implementer makes tests pass, one at a time
                 AUDIT       mechanical git diff check — role boundaries enforced
                 VALIDATE    validator reviews atomic/integration/system correctness
                 REVIEW      fresh-context reviewer dispatches to external models
                 FIX         correction loop if REVISE verdict (max 1 cycle)
```

### Three Pillars

**Fresh-context review** — The reviewer runs in a separate `claude -p` session with no implementation memory. Defeats model sycophancy.

**External model dispatch** — Review payloads are sent to Gemini, OpenAI, and/or Grok for independent cross-validation. Responses presented verbatim with attribution.

**Hook-based enforcement** — `pretool-guard.sh` freezes build artifacts during execution. `post-edit-test-runner.sh` runs tests after every file edit. Post-step audit verifies agent role boundaries via git diff.

### Agents

| Agent | Role |
|-------|------|
| **scoper** | Gathers requirements interactively, writes scope + config |
| **security-reviewer** | Independent security analysis before implementation |
| **test-architect** | Writes failing tests from requirements (RED phase) |
| **implementer** | Makes tests pass with minimal code (GREEN phase) |
| **validator** | Fresh-context atomic/integration/system validation |
| **reviewer** | Fresh-context review with external model dispatch |

## Configuration

Each project gets a `forge/config.md` during scoping:

```markdown
# Config

Test Command: npm test
Test Location: tests/
Quality Command: npx tsc --noEmit && npx eslint .
Maturity: prototype | mvp | production
Risk Tolerance: high | moderate | low

## Review Providers
gemini, openai

## Models
gemini: gemini-3.1-pro-preview (thinking: high)
openai: gpt-5.4 (thinking: high)

## Key Files
src/main.ts
```

## Requirements

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- API keys for external review providers (optional but recommended):
  - `GEMINI_API_KEY` — Google Gemini
  - `OPENAI_API_KEY` — OpenAI
  - `XAI_API_KEY` — Grok
- `jq` (used by dispatch scripts)

## Structure

```
.claude-plugin/plugin.json      # Plugin manifest
CLAUDE.md                       # Instructions for Claude
agents/                         # 6 agent definitions (markdown)
skills/                         # 4 skill definitions (markdown)
conventions/                    # Dispatch protocol
hooks/                          # PreToolUse guard + PostToolUse test runner
scripts/                        # Unified API dispatch + model defaults
```

## License

MIT
