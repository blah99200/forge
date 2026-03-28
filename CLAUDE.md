# CLAUDE.md

## What This Is

Forge is a Claude Code plugin — a lean TDD orchestration system. It coordinates 6 specialized agents through RED → GREEN → VALIDATE → REVIEW with fresh-context cross-model review. This repo contains agent definitions, skills, hooks, and bash scripts. No build step, no package manager.

## Architecture

**Agents:** scoper → security-reviewer → test-architect → implementer → validator → reviewer

**Skills:** `forge:scope`, `forge:build`, `forge:validate`, `forge:review`

**Load-bearing infrastructure (bash):**
- `scripts/review-dispatch.sh` — Unified API dispatch to Gemini/OpenAI/Grok
- `scripts/model-dispatch-lib.sh` — Shared functions + payload security redaction
- `hooks/pretool-guard.sh` — Blocks writes to forge/ metadata and plugin dirs during builds
- `hooks/post-edit-test-runner.sh` — Auto-runs tests after every edit
- Post-step audit (build skill) — Verifies role boundaries via `git diff` after each phase

**Convention:** `conventions/dispatch-protocol.md` — mechanical dispatch steps

## Key Rules

- Use dedicated tools: Read not cat, Write not echo, Edit not sed, Grep not grep, Glob not find. Bash for git, tests, and external CLIs only.
- Plugin root: resolve via `.claude-plugin/plugin.json`
- Fresh-context review: `env -u CLAUDECODE claude -p` — no `--bare` (breaks OAuth)
- Git: feature branches push freely, main/dev ask first. Never use `-i` flags.
- Agent definitions: YAML frontmatter + markdown. Each agent ~500-1000 tokens max.
- Skill definitions: YAML frontmatter. User-facing skills include `argument-hint`.
- Every token of scaffolding must earn its place against a specific failure mode.
