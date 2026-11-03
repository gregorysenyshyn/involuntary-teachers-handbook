# Codebase Map (`CODEBASE_MAP.md` Template)

> **Agent Instruction:** Read this file *before* running repository-wide `grep` or file searches. When modifying a specific service, run `python3 tools/pack_context.py <service_name> --stdout` to load its directory tree, AST symbol outline, and source files in a single tool call.

---

## 1. Microservices & Local Development Ports

| Service (`apps/<name>`) | Local Port | Role & Primary Responsibility | Key Entrypoints |
| :--- | :---: | :--- | :--- |
| **`auth`** | `8081` | OIDC / JWT token issuance, magic links, & RBAC middleware | `apps/auth/src/handler.py` |
| **`billing`** | `8082` | Stripe webhook ingestion, ledger entries, & invoice PDFs | `apps/billing/src/webhook.py` |
| **`courseware`** | `8083` | Student assessment submission & AI rubric evaluation | `apps/courseware/src/submit.py` |
| **`security`** | `N/A` | Error ticket SNS processor (`ErrorTicketCreator`) & KMS keys | `apps/security/src/error_ticket.py` |
| **`e2e-monitor`** | `N/A` | Scheduled 6 AM synthetic canary across critical user journeys | `apps/e2e-monitor/src/canary.py` |

---

## 2. Shared Libraries (`common/`)

| Module | Purpose |
| :--- | :--- |
| `common/shared/pii_utils.py` | Mandatory PII masking (`mask_email`, `mask_phone`) before logging or error reporting |
| `common/shared/error_reporter.py` | `@github_error_reporter` Lambda decorator publishing sanitized tracebacks to `ErrorTicketsTopic` |
| `common/shared/test_harness.py` | `patch_boto3()` in-memory DynamoDB/S3/SNS adapter for `0.5s` offline unit tests |

---

## 3. Cross-Service Event & API Contracts

### `courseware` ➔ `marking-queue` (`POST /v1/submissions`)
- **Producer:** `apps/courseware/src/submit.py`
- **Consumer:** `apps/marking-queue/src/ingest.py`
- **Required Payload Keys:** `student_id` (UUID), `course_code` (str), `assessment_id` (str), `artifact_s3_key` (str), `submitted_at` (ISO-8601 UTC).
- **Idempotency Key:** `PK = SUB#{student_id}`, `SK = {course_code}#{assessment_id}`.
