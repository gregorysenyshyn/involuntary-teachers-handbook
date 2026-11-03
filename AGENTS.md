# AI Agent Instructions (AGENTS.md)

This file defines the architectural, security, and context-hygiene rules for AI coding agents working in this repository.

## Core Engineering Philosophy

1. **Privacy & Security First**: Never log raw PII (emails, phone numbers, addresses, full names) in structured logs or cloud log streams. Always apply PII masking utilities before emitting logs or error telemetry.
2. **Gold-Standard Solutions Over Quick Hacks**: Preventing the accumulation of technical debt is our primary overarching concern. **Fixing things immediately is less important than identifying underlying suboptimal patterns and implementing an enterprise-grade solution.**
3. **Zero Silent Fallbacks**: Silent `try/except: pass` blocks and speculative fallback branches hide bugs from both humans and agents. Fail fast with descriptive exceptions that serialize the exact failure into the context window.
4. **Red-Team the Request**: Do not act as a passive order-taker. Actively evaluate the user's request for architectural edge cases, simpler subtractive alternatives, or security blind spots, and propose a better approach when you see one.
5. **Write for LLM Comprehensibility**: Observe conventions that require fewer reasoning tokens—explicit types, flat control flow, single-responsibility modules, and declarative JSON/YAML manifests over deeply nested imperative scripts.

---

## Context Window Sanitation & Navigation

6. **Consult the Maps First**: Always read `CODEBASE_MAP.md`, `SCHEMA_DICTIONARY.md`, and `docs/adr/` before making structural changes, cross-service calls, or database queries. Avoid unbounded repository-wide `grep` searches.
7. **Use the AST Context Packer**: When reviewing or modifying a microservice or shared module, run `python3 tools/pack_context.py <path>` to generate a token-efficient context bundle with an ASCII directory tree and AST symbol outlines rather than reading dozens of files individually.
8. **Proactive Token-Efficiency & Architecture Alerts**: Actively monitor your own tool consumption and context friction. If during any task you encounter any of the following **friction triggers**:
   - Reading or editing a monolithic file with **>1,000 lines**
   - Needing more than **3 exploratory searches** to understand an undocumented cross-service contract or payload schema
   - Touching repetitive HTML/code boilerplate or hardcoded data dictionaries that should be declarative JSON/YAML manifests
   - Encountering stale, abandoned, or unreferenced files that add context clutter
   **You must flag this friction in your response and proactively propose a high-ROI, token-saving refactor or documentation fix.** Do not quietly suffer through 3,000-line monoliths; speak up and offer a cleaner solution.

---

## Verification, Diagnostics & Hygiene

9. **Living Test Suite Maintenance**: Keeping the test suite green and up-to-date is a non-negotiable verification requirement.
   - Every bug fix or new capability must be accompanied by new or updated unit/integration tests.
   - Run the fast local test suite (`pytest`) before declaring victory.
   - Never disable, delete, or weaken failing assertions just to make a build pass; resolve the root cause or update the assertion to match a deliberate contract change.
10. **The 3-Strike Diagnostic Circuit Breaker**: If a bug, test failure, or build error is not resolved after **3 attempts**, stop guessing and code-thrashing. Step back, re-read the exact stack trace, check recent logs, and present an analytical root-cause diagnosis with options before making further edits.
11. **Backwards-Compatible Schema Evolution**: When modifying database entities, API payloads, or session tokens, never assume a clean database. Existing production records must remain readable via dual-read logic (`data.get("new_key") or data.get("old_key")`), and `SCHEMA_DICTIONARY.md` must be updated in the same commit.
12. **Diff Hygiene & Minimal Blast Radius**:
    - Never reformat unrelated lines or run unprompted whole-file linters across files you weren't asked to touch.
    - Run `git diff --stat` before concluding a task to verify only intended files and lines were touched.
    - Remove temporary debugging statements (`console.log`, `print()`, commented-out dead code) before finishing.
13. **Institutional Memory via Micro-ADRs**: When introducing a structural change, a new cross-service interaction, or a non-obvious architectural pattern, document the rationale in a concise 1-page ADR in `docs/adr/` so subsequent agents do not inadvertently reverse the design.
14. **Continuous Repo Hygiene ("Propose, Don't Silently Purge")**: When navigating the codebase, actively flag out-of-date documentation, orphaned scripts, or superseded modules with 0 active references and propose them for deletion with a concise rationale.
15. **Strictly Isolated Stacks (SSM Parameters Over `Fn::ImportValue`)**:
    - Never use CloudFormation `Fn::ImportValue` cross-stack exports or hardcode cross-account IAM ARNs in config files. Cross-stack exports create deployment deadlocks (`Export X cannot be updated as it is in use by Stack Y`) that cause coding agents to thrash templates across the repo.
    - Every microservice stack must be strictly isolated and decoupled via **SSM Parameter Store** (`AWS::SSM::Parameter` + `AWS::SSM::Parameter::Value<String>`). When renaming an SSM parameter, globally search the monorepo for all consumers and update them in the same commit.
16. **Pre-Deploy Guardrails & Early Validation Debugging**:
    - Always run `verify-imports` before deploying Lambda functions so broken imports fail locally in `0.2s`.
    - When a stack fails with `EarlyValidation::ResourceExistenceCheck` or `ROLLBACK_COMPLETE`, never guess—run `python3 tools/diagnose_stack.py diagnose-early-validation` or `explain-failure` to serialize the exact missing resource into the context window.
