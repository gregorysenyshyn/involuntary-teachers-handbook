# Socratic Red-Teaming Prompts (`prompts/SOCRATIC_RED_TEAMING.md`)

Trained on human code, LLMs suffer from **addition bias on steroids** (Adams et al., *Nature* 2021): left to their own devices, they will solve every problem by adding another wrapper, fallback branch, or helper file rather than removing the single brick causing the problem.

Once your test suite is green, stop issuing only imperative feature requests and run these **Socratic Red-Teaming prompts** at the end of a task or sprint.

---

## 1. The Architectural Smell-Test Prompt
> **"What is the worst part of this repo?"**
>
> *Follow-up:* "Rank the top 3 sources of accidental complexity, brittle coupling, or silent failure in this module, and propose a subtractive refactor that deletes more lines than it adds while keeping all tests green."

---

## 2. The Gold-Standard Rubric Prompt
> **"Does this solution represent a 'gold standard' implementation?"**
>
> *Follow-up:* "Red-team the code we just wrote. Are there any silent fallbacks, dual-state bugs, missing edge-case assertions, or places where we patched a symptom instead of fixing the underlying data contract?"

---

## 3. The Context Window Sanitation Prompt
> **"Were there any parts of this task that were particularly token-inefficient?"**
>
> *Follow-up:* "Which files, undocumented contracts, or noisy grep searches burned the most context window during this task? Propose a concrete update to `CODEBASE_MAP.md`, `SCHEMA_DICTIONARY.md`, or a file split (`<1,000` lines) so the next agent can solve a similar task in 1 read."

---

## 4. The Subtractive / DRY Audit Prompt
> **"Do you see any opportunities to make the codebase more DRY or delete dead weight?"**
>
> *Follow-up:* "Identify any duplicate helper logic, hardcoded data dictionaries that should be declarative JSON/YAML manifests, or unreferenced files with 0 active callers. List them with your rationale before we purge them."
