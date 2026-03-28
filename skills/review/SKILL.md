---
name: forge:review
description: Standalone code review — dispatch to external models for independent cross-validation
argument-hint: "<optional: files or description to review>"
---

Run a standalone review without prior scoping.

## Setup
1. Create `forge/` directory if needed
2. Stage scripts to `forge/.scripts/`:
   - `<plugin-root>/scripts/review-dispatch.sh` → `forge/.scripts/review-dispatch.sh`
   - `<plugin-root>/scripts/model-dispatch-lib.sh` → `forge/.scripts/model-dispatch-lib.sh`
   - `<plugin-root>/scripts/models.json` → `forge/.scripts/models.json`
3. If no `forge/config.md` exists, check available API keys and create a minimal config with defaults

## Assemble Review Prompt
1. Read `<plugin-root>/agents/reviewer.md`
2. Read `<plugin-root>/conventions/dispatch-protocol.md`
3. Run `git diff` (or diff against main/dev) to capture changes, or use user-specified files
4. Add task block containing: config.md contents, the diff or source files, and any scope/security context if available. Note: "Dispatch scripts are pre-staged at forge/.scripts/. If no scope.md exists, review for code quality and intent alignment rather than REQ-ID coverage."
5. Write assembled prompt to `forge/review-prompt.md`

## Invoke
```bash
env -u CLAUDECODE claude -p \
  --effort max \
  --allowedTools "Read,Grep,Glob,Write,Bash(bash forge/.scripts/review-dispatch.sh:*),Bash(rm -f forge/*-raw.md),Bash(rm -f forge/*-payload*.md)" \
  < forge/review-prompt.md
```

## Cleanup
```bash
rm -f forge/.scripts/review-dispatch.sh forge/.scripts/model-dispatch-lib.sh forge/.scripts/models.json
rmdir forge/.scripts/ 2>/dev/null || true
rm -f forge/review-prompt.md forge/*-payload*.md forge/*-raw.md
```
