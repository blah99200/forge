---
name: forge:validate
description: Standalone validation — atomic, integration, and system checks without a full build
argument-hint: "<optional: files or description to validate>"
---

Run standalone validation without a full build cycle. Delegate to the **validator** agent.

If `forge/scope.md` exists, the validator checks implementation against requirements. If not, it checks code quality, contracts, and correctness.

If `forge/security.md` exists, the validator verifies CRITICAL/HIGH findings are addressed.

If `forge/config.md` has a `Quality Command:`, run it first — mechanical gate before model analysis.

The validator writes findings to `forge/validation.md` with owner routing (`implementer` / `test-architect` / `user`).
