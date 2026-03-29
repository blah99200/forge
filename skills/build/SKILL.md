---
name: forge:build
description: Run the TDD build cycle — security, RED, GREEN, VALIDATE, REVIEW with correction loop
argument-hint: "<optional: initiative name or --resume>"
---

## Prerequisites
- `forge/scope.md` must exist (run `/forge:scope` first)
- `forge/config.md` must exist
- Offer to create a `feat/<name>` branch if on main/dev

## Build Activation
Create `forge/.build-active` marker at the start of the build. This signals to `pretool-guard.sh` that scope.md and config.md should be frozen. The marker is removed during cleanup.

## Stage Scripts
Read and write to `forge/.scripts/`:
- `<plugin-root>/scripts/review-dispatch.sh` → `forge/.scripts/review-dispatch.sh`
- `<plugin-root>/scripts/model-dispatch-lib.sh` → `forge/.scripts/model-dispatch-lib.sh`
- `<plugin-root>/scripts/models.json` → `forge/.scripts/models.json`

## Security Review (once, before phases)
Delegate to **security-reviewer** agent. It reads scope.md and config.md, explores the codebase, and writes `forge/security.md`. If no meaningful security surface, it writes `forge/security.md` with "No security surface identified for this scope." This runs once — not per-phase.

## Pipeline

If scope.md has no phases section, treat the entire scope as a single phase.

For each phase in scope.md:

Record the current commit SHA before starting the phase: `git rev-parse HEAD 2>/dev/null || echo "4b825dc642cb6eb9a060e54bf8d69288fbee4904"` → store as `PHASE_START_SHA`. (The fallback is git's empty tree hash for greenfield repos with no commits.)

### 1. RED Phase
Delegate to **test-architect** agent. It writes failing tests from scope requirements and security findings. Each test committed with `RED: [REQ-XXX]` prefix.

### 2. GREEN Phase
Delegate to **implementer** agent. It writes production code to make tests pass, committing each with `GREEN: [REQ-XXX]`.

**If any agent surfaces an issue during the build** (e.g., requirement more complex than scoped, test seems fundamentally wrong), **pause and present to user** for decision before continuing.

### 3. Post-Step Audit
After GREEN, mechanically verify constraints via `git diff PHASE_START_SHA..HEAD`:
- Implementer did not modify test files (check for test file paths in GREEN: commits)
- Test-architect did not modify production code (check for non-test paths in RED: commits)
- No protected forge artifacts were modified

If violations found: revert the offending changes and re-delegate to the correct agent.

Run Quality Command from config.md (if configured). If it fails, delegate to **implementer** to fix before proceeding.

### 3b. Smoke Test (conditional)
When Key Files include platform artifacts (`skills/`, `hooks/`, `.claude-plugin/`), verify the built artifacts actually load before proceeding:
- **Skill files**: run `claude -p "list your skills" --allowedTools "" --max-turns 1` and confirm the new/modified skill appears in output. If it doesn't, the skill has invalid frontmatter or isn't discoverable — flag before VALIDATE.
- **Hook files**: verify `hooks.json` parses as valid JSON (`python3 -c "import json; json.load(open('hooks.json'))"` or equivalent).
- **Plugin config**: verify `plugin.json` parses as valid JSON.

If smoke test fails: delegate to **implementer** to fix the artifact, then re-run the smoke test. Max 2 attempts — if still failing, pause and present to user.

### 4. VALIDATE Phase
Delegate to **validator** agent as a subagent (context fork — isolated from GREEN work but not a separate `claude -p` session).

Pass to the validator:
- `forge/scope.md`, `forge/security.md` (if exists)
- Changed files and their REQ-IDs (from `git log PHASE_START_SHA..HEAD`)
- The changed source and test files

The validator performs atomic → integration → system analysis in one pass and writes `forge/validation.md` with structured findings. Each finding has an **owner**: `implementer`, `test-architect`, or `user`.

**If CLEAN:** proceed to REVIEW.

**If findings with owner `implementer`:** delegate fixes to **implementer**, run quality command, commit with `VALIDATE: description`.

**If findings with owner `test-architect`:** delegate test fixes to **test-architect**, re-run affected tests to confirm they fail, then delegate to **implementer** to make them pass. Commit with `VALIDATE: description`.

**If findings with owner `user`:** present to user for decision before proceeding.

After fixes: re-run validator once. If still not clean, proceed to REVIEW with findings noted — the external review will catch remaining issues.

Max 2 validation cycles total (validate → fix → re-validate → done).

### 5. REVIEW Phase (external, fresh-context)
Assemble review prompt:
1. Read `<plugin-root>/agents/reviewer.md`
2. Read `<plugin-root>/conventions/dispatch-protocol.md`
3. Run `git diff PHASE_START_SHA..HEAD` to capture all changes for this phase
4. Add task block containing: forge/scope.md contents, forge/config.md contents, forge/security.md contents (if exists), forge/handoff.md dismissed-findings section (if exists, multi-phase only), forge/validation.md (if unresolved findings remain after validation cycles), the git diff output, and key source files
5. Write assembled prompt to `forge/review-prompt.md`

Invoke fresh-context review:
```bash
env -u CLAUDECODE claude -p \
  --effort max \
  --allowedTools "Read,Grep,Glob,Write,Bash(bash forge/.scripts/review-dispatch.sh:*),Bash(rm -f forge/*-raw.md),Bash(rm -f forge/*-payload*.md)" \
  < forge/review-prompt.md
```

Read `forge/review.md` for verdict. If `forge/review.md` does not exist (fresh-context crashed or timed out), report the failure and ask the user whether to retry or skip review.

### 6. Correction (if REVISE)
Read `forge/issues.md`. Route by owner:
- `[owner: implementer]`: delegate to **implementer** to fix, commit with `FIX: description`
- `[owner: test-architect]`: delegate to **test-architect** to fix tests, then delegate to **implementer** to make fixed tests pass. Commit with `FIX: description`.
- `[owner: user]` or `[ARCHITECTURAL]`: present to user for decision

After fixes: re-run VALIDATE + REVIEW (max 1 correction cycle).

### 7. Deferred-Test Coverage (if applicable)
After the correction loop, check scope.md for `(deferred-test)` requirements that the implementer implemented but have no tests. For any that are now testable (implementation made the behavior concrete), delegate to **test-architect** to write retroactive coverage tests, then delegate to **implementer** to make them pass. Commit with `COVERAGE: [REQ-XXX] description`.

### Between Phases
- Git commit checkpoint
- Run the full test suite to catch cross-phase regressions before proceeding
- Write `forge/handoff.md` with key decisions, failed approaches, and constraints from this phase. This survives context management and is read by agents in the next phase to avoid retrying failed strategies.
- If prior phase review dismissed any findings, note them in `forge/handoff.md` so subsequent reviews don't re-flag the same issues.
- Context is managed automatically; for very long builds, the user may `/compact` manually

## Resume
Read `git log --oneline` for `RED:` / `GREEN:` / `VALIDATE:` / `FIX:` / `COVERAGE:` prefixes to determine completed work. Skip completed requirements.

## Cleanup
After build completes (or on failure):
```bash
rm -f forge/.build-active
rm -f forge/.scripts/review-dispatch.sh forge/.scripts/model-dispatch-lib.sh forge/.scripts/models.json
rmdir forge/.scripts/ 2>/dev/null || true
rm -f forge/review-prompt.md forge/*-payload*.md forge/*-raw.md forge/validation.md forge/handoff.md
```
