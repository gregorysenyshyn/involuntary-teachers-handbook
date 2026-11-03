# Socratic Red-Teaming Prompts (`prompts/SOCRATIC_RED_TEAMING.md`)

Because LLMs are sycophantic next-token predictors, leading prompts that presume a specific flaw will cause the model to manufacture whatever answer it thinks you want to hear. True Socratic questioning is **open-ended and non-leading**: ask the simple, naked question so the agent evaluates the codebase—and your own assumptions—without your thumb on the scale.

Once your test suite is green, stop issuing only imperative feature requests and ask these **Socratic Red-Teaming questions** at the end of a session:

---

## 1. The Architectural Smell-Test
> **"What is the worst part of this repo?"**

---

## 2. The Gold-Standard Rubric Check
> **"Does this solution represent a 'gold standard' implementation?"**

---

## 3. The Context Window Sanitation Check
> **"Were there any parts of this task that were particularly token-inefficient?"**

---

## 4. The Subtractive / DRY Check
> **"Do you see any opportunities to make the codebase more DRY or reduce redundancy?"**

---

## 5. The Developer Mirror (Preventing Meat Puppets)
> **"Based on my input in this session, how would you rate my level of sophistication as a developer? Do my responses indicate any significant misunderstanding of concepts or implementations? Are there areas I should learn more about in order to be able to better assess this solution?"**
