# The Involuntary Teachers' Handbook
### A Classroom Survival Guide for Software Engineers Managing AI Agents

> **Companion Repository to the [waterloo.dev](https://waterloo.dev) Talk:**  
> *"How I'm Trying to Prevent Kids From Becoming Meat Puppets for a Chat Bot"* — **Gregory Senyshyn** ([gregorysenyshyn.com](https://gregorysenyshyn.com))

---

## 🎓 Welcome, Involuntary Teachers

Once you stop writing syntax by hand, your job changes overnight: **every software engineer orchestrating AI agents is now an involuntary high school teacher.**

The "tokenmaxxing" era didn't stall out because the models weren't smart enough. It stalled out because teams tried to build **Henry Ford's faster horse**—bolting a 1,000-horsepower LLM onto a codebase designed for human typing speed, tribal knowledge, 3,000-line monoliths, and manual QA.

As someone who has taught high school since 2007 and built on AWS since 2013, here is the secret: **underneath all the mystique, coding agents behave practically like teenagers:**

1. **Oracular Wisdom ("Trust me, bro"):** You will always get a confident answer, even when you have no idea how it was arrived at—or whether you'd get a totally different answer five minutes from now.
2. **Short Attention Span:** Even when your directions are crystal clear, they might decide you meant to put the cutlery in the microwave and go eat some Tide pods. Dump 50 random files into their context window and they immediately lose the plot.
3. **Never Cleans Their Room:** Trained on human code, LLMs have addition bias on steroids—leaving behind a sprawling mess of wrapper functions, silent fallbacks, and duplicate logic unless you personally check the rubric.

You don't fix a teenager by yelling harder at the prompt. You prevent mutiny the same way a teacher runs a classroom:
1. **Design Your Course** (Good exemplars, fail-fast descriptive feedback, and `0.5s` self-assessment tools)
2. **Prepare Your Materials** (Isolated sandbox classroom, real agent email address, 100% IaC visibility, and context window sanitation)
3. **Check the Rubric** (End-to-end verification, overnight self-healing loops, and Socratic red-teaming)

---

## 📊 The Report Card (Production Case Study)

Using the exact classroom rules, AST context packers, and self-healing loops in this handbook, a solo teacher built and operates a complete, Ministry-inspected online private high school platform ([Asynchronous Academy](https://asyncacademy.ca)):

| Metric | Production Reality |
| :--- | :--- |
| **Architecture Footprint** | **29 Microservices** • 57 ARM64 Lambdas • 14 DynamoDB Tables • Full CI/CD & IaC |
| **Build Timeline** | **~8 Months** on evenings & weekends while working a full-time day job |
| **Cloud Hosting Cost** | **<$25/month per environment** (`dev` & `prod` in AWS `ca-central-1` with 4-layer WORM backups & budget kill switches) |
| **AI Coding Spend** | **$25/month Google AI Plan** (Zero $1,000/month API token overages) |

> **🍎 PD Days for Grown-Ups (Repo Audits & Team Training):**  
> *I don't just work with kids—I also do trainings for grown-ups.* Book an offline **Codebase Report Card (Async Audit & Written Roadmap)**, a live **1-Day PD Day (Socratic Red-Teaming Clinic)** on your existing repo, or the flagship **4-Part Monorepo Makeover** at **[gregorysenyshyn.com/training](https://gregorysenyshyn.com/training)**.

---

## 🎒 What's Inside Your Teacher's Kit

```text
involuntary-teachers-handbook/
├── AGENTS.md                                 # 1. The Course Syllabus (16 drop-in classroom rules)
├── tools/
│   ├── pack_context.py                       # 2. The Study Guide Generator (Zero-dep AST context packer)
│   ├── diagnose_stack.py                     # 3. Descriptive Feedback (EarlyValidation & import debugger)
│   ├── validate_repo_hygiene.py              # 4. Mechanical Hall Monitor (Enforces AGENTS.md, PII masking, SSM & JS/Py contracts)
│   ├── generate_report_card.py               # 5. The Codebase Report Card (Grades any repo A+ to F on Agent Readiness)
│   ├── test_impacted.py                      # 6. Diff-Aware Test Runner (Traces git diff + shared layer imports to impacted suites)
│   └── sam_moto_harness.py                   # 7. Zero-Config SAM -> Moto Test Harness & strict_patch (Prevents Lying MagicMocks)
├── templates/
│   ├── CODEBASE_MAP.md                       # 8. The Campus Map (1-read microservice & port directory)
│   └── SCHEMA_DICTIONARY.md                  # 9. The Vocabulary List (1-read database & payload contracts)
├── examples/
│   └── self-healing-loop/                    # 10. Self-Grading Homework (Overnight Auto-Heal Loop)
│       ├── error_reporter.py                 #    Lambda @github_error_reporter + PII-masked SNS publisher
│       ├── client-error-listener.js          #    Browser JS / unhandledrejection / CSP error forwarder
│       └── gemini-agent.yml                  #    GitHub Actions workflow (Issue -> OIDC Sandbox -> PR)
└── prompts/
    └── SOCRATIC_RED_TEAMING.md               # 10. Checking the Rubric (Prompts to fight addition bias)
```

---

## 🧱 Two Unsung Keys to Agentic Velocity

### A. Assigned Seating: Strictly Isolated Stacks via SSM (Zero `Fn::ImportValue`)
If you tie 29 teenagers' shoelaces together (`Fn::ImportValue` cross-stack exports), when one trips, the entire classroom hits the floor. In CloudFormation/CDK, cross-stack exports create deployment deadlocks (`Export X cannot be updated as it is in use by Stack Y`) that cause coding agents to panic and thrash templates across the repo.
- **The Rule:** Every microservice is a strictly isolated stack. Cross-stack and cross-account references are passed exclusively via **SSM Parameter Store** (`AWS::SSM::Parameter` + `AWS::SSM::Parameter::Value<String>`), enforced by a pre-deploy guardrail that blocks hardcoded ARNs. An agent can deploy, refactor, or roll back any single service in 30 seconds with zero blast radius.

### B. Descriptive Feedback: The `EarlyValidation` & Pre-Deploy Import Debugger ([`tools/diagnose_stack.py`](./tools/diagnose_stack.py))
When AWS CloudFormation rejects a ChangeSet with `EarlyValidation::ResourceExistenceCheck`, it hides *which* SSM parameter, Secret, KMS key, S3 bucket, or Hosted Zone is missing. Left alone, an agent will burn 20 tool calls guessing.
- **`diagnose-early-validation`:** Parses `template.yaml`, checks every referenced SSM parameter/secret/bucket via Boto3, and queries CloudFormation `describe_events` for `VALIDATION_ERROR` hook reasons—printing the exact missing resource in 1 second.
- **`verify-imports`:** Dry-run imports every compiled Python Lambda handler in an isolated `sys.path` before deployment so a `ModuleNotFoundError` fails locally in `0.2s` instead of after a 4-minute cloud deploy.
- **`explain-failure`:** Walks `describe_stack_events` and strips out downstream cancellation noise to print only the root-cause `*_FAILED` resource.

---

## 🚀 First Day of School: Grade Your Repo in 3 Seconds

Run the zero-dependency **Codebase Report Card Generator** locally against any Python, TypeScript, or JavaScript repository (no clone or API key required):

```bash
curl -sSL https://raw.githubusercontent.com/gregorysenyshyn/involuntary-teachers-handbook/main/tools/generate_report_card.py | python3 - /path/to/your/repo
```

Or customize your team's monthly AI spend and headcount to calculate your exact **Monthly Context & Rework Tax**:

```bash
python3 tools/generate_report_card.py /path/to/your/repo --monthly-ai-spend 15000 --team-size 12
```

### 1. Hand Out the Syllabus (`AGENTS.md`)
Copy [`AGENTS.md`](./AGENTS.md) into the root of your repository (and/or `.github/copilot-instructions.md`, `CLAUDE.md`, `.cursorrules`). It enforces:
- **Zero Silent Fallbacks:** Fail fast with descriptive exceptions that serialize the exact failure into the agent's context window.
- **Proactive Context-Friction Alerts:** Any time an agent touches a file `>1,000` lines or needs `>3` exploratory searches to trace a contract, it must raise its hand, flag the friction, and propose a structural split.
- **The 3-Strike Diagnostic Circuit Breaker:** After 3 failed attempts at a test or build error, the agent must stop guessing, inspect logs, and present a root-cause diagnosis before editing again.

### 2. Curate the Reading Packet (`tools/pack_context.py`)
Don't dump 50 random books on a teenager's desk. Instead of letting an agent burn 40,000 tokens running `grep` and reading 30 files one by one, bundle an entire microservice or module—complete with an ASCII directory tree and Python AST class/method signatures—in one command:

```bash
python3 tools/pack_context.py path/to/service --stdout
```

### 3. Post the Campus Map (`CODEBASE_MAP.md` & `SCHEMA_DICTIONARY.md`)
Copy [`templates/CODEBASE_MAP.md`](./templates/CODEBASE_MAP.md) and [`templates/SCHEMA_DICTIONARY.md`](./templates/SCHEMA_DICTIONARY.md) into your repo root so your agent gets the entire world model in 1 read instead of hallucinating cross-service contracts.

### 4. Grade the Rubric Automatically (Self-Healing Loop & Socratic Prompts)
See [`examples/self-healing-loop/`](./examples/self-healing-loop/) for the complete 5-step production pattern (`SNS ➔ GitHub Issue ➔ Label Webhook ➔ OIDC Sandbox Reproduction ➔ Passing PR by Morning`) and [`prompts/SOCRATIC_RED_TEAMING.md`](./prompts/SOCRATIC_RED_TEAMING.md) for the Socratic cleanup prompts that make agents clean their room.

---

## 🍎 Professional Development (PD Days for Grown-Ups)

**I don't just work with kids—I also do trainings for grown-ups.**

If your engineering team is spending thousands on cloud infrastructure and AI seats without seeing the velocity gains you were promised, I offer three ways to help:
- **The Codebase Report Card (Offline Audit & Written Roadmap):** An asynchronous deep-dive into your repository, AI token burn, and cloud bill—delivering a candid, jargon-free written assessment, a prioritized 30-day action plan, and a 45-minute walkthrough call.
- **The 1-Day PD Day (Standalone Socratic Red-Teaming Clinic):** A single hands-on day directly on your existing repo with zero infrastructure changes—installing `AGENTS.md`, AST context packing, and live Socratic Red-Teaming.
- **The 4-Part Monorepo Makeover (Flagship):** Standing up a parallel, ultra-low-cost, agentically-managed monorepo over four weekly sessions without ever touching your live production environment so your team falls back in love with your code.

👉 **[Book a Codebase Report Card, 1-Day PD Day, or 4-Part Monorepo Makeover at gregorysenyshyn.com/training](https://gregorysenyshyn.com/training)**
