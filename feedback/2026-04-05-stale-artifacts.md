# Feedback: Stale Artifact Contamination in Forge Workflow

**Date:** 2026-04-05
**Source:** Real session — Admin Portal Redesign scoping attempt
**Severity:** High — caused wrong-project scope output and invalid model config

---

## Issue 1: Scoper adopted stale scope instead of writing fresh

**What happened:** The user asked to scope an "Admin Portal Redesign". The scoper agent found an existing `forge/scope.md` on disk from a completely different prior initiative ("Cross-Portal UI Consistency Fixes"). Instead of recognizing this was stale and writing a fresh scope, the scoper adopted the existing file's content and produced a scope for the wrong project entirely.

**Root cause:** `forge/` is in `.gitignore`, so `git status` shows clean even when stale artifacts exist. There's no mechanism in the scoper agent to detect that an existing scope.md is from a different initiative, or to clear stale artifacts before starting.

**Recommended fix:** The scope skill (or the scoper agent definition) should:
1. Check if `forge/scope.md` already exists before starting
2. If it does, compare its title/summary against the current request
3. If they don't match, warn and either delete or ask before overwriting
4. Better yet: the scope skill should ALWAYS start by clearing `forge/scope.md` and `forge/config.md` — a new scope run means a fresh start. Archive old artifacts to a timestamped or initiative-named subdirectory if preservation is needed.

---

## Issue 2: Config.md copied outdated model names

**What happened:** When writing `forge/config.md`, the session agent (main Claude) copied model names from the stale config that was already on disk. These were outdated names (`gemini-2.5-pro`, `o3`, `grok-3`) that don't match the current `scripts/models.json` defaults (`gemini-3-flash-preview`, `gpt-5.4-mini`, `grok-4.20-0309-reasoning`).

**Root cause:** There's no validation that model names in config.md correspond to entries in models.json. The scoper writes whatever it wants. The session agent may also write config.md manually (as happened here) without checking models.json.

**Recommended fix:**
1. The scope skill or scoper should read `scripts/models.json` and use the `deep` tier as the default when generating config.md
2. The review-dispatch.sh script could validate model names against models.json before dispatching, and fail fast with a helpful error if a model name isn't recognized
3. Consider making the `## Models` section in config.md optional — if omitted, the review skill defaults to the `deep` tier from models.json. This avoids the entire class of stale-model-name bugs.

---

## Issue 3: No artifact lifecycle management

**What happened:** After a prior initiative was completed, its forge artifacts (`scope.md`, `config.md`, `review.md`, `security.md`) were left in `forge/` on disk. Since `forge/` is gitignored, these are invisible to git but very visible to agents that read the filesystem.

**Root cause:** There's no cleanup step after a build/review cycle completes. The `forge/` root is a shared workspace with no concept of "this belongs to initiative X".

**Recommended fix:**
1. Add a `forge:clean` skill or a cleanup step at the END of `forge:build` and `forge:review` that moves artifacts into a named subdirectory (e.g., `forge/completed/css-consistency-2026-04-01/`)
2. OR at the START of `forge:scope`, always wipe `forge/` root artifacts (scope.md, config.md, review.md, security.md, validation.md) before beginning
3. The `forge/` directory structure should have documented ownership: root = active initiative, subdirectories = archived

---

## Summary

The common thread is that Forge trusts the filesystem state without verification. Agents read what's on disk and assume it's relevant to the current task. This needs to be hardened with:

- **Stale artifact detection** at scope start
- **Model name validation** against models.json
- **Artifact lifecycle management** (clean start or archive-on-complete)

These are mechanical checks that should not be left to agent judgment — they should be enforced by the skills/scripts.
