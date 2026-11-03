# Schema Dictionary (`SCHEMA_DICTIONARY.md` Template)

> **Agent Instruction:** Consult this dictionary before writing database queries or modifying API payloads. Whenever you add or evolve an attribute, you **must** implement dual-read backwards compatibility (`record.get("new_key") or record.get("old_key")`) and update this file in the same commit.

---

## 1. Core Single-Table Design (`AppCoreTable`)

- **Partition Key (`PK`):** String (`ENTITY#{id}`)
- **Sort Key (`SK`):** String (`METADATA` or `CHILD#{timestamp}#{id}`)
- **GSI1 (`GSI1PK` / `GSI1SK`):** Inverted lookup index by status or organization
- **TTL Attribute:** `expires_at` (Unix epoch seconds)

### Entity Patterns

| Entity | `PK` | `SK` | `GSI1PK` | `GSI1SK` | Key Attributes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **User Profile** | `USER#{user_id}` | `METADATA` | `ORG#{org_id}` | `USER#{created_at}` | `email_hash`, `role`, `status`, `created_at` |
| **Submission** | `USER#{user_id}` | `SUB#{course}#{unit}` | `STATUS#{status}` | `SUB#{submitted_at}` | `s3_key`, `rubric_version`, `score`, `feedback_summary` |
| **Error Ticket Dedup** | `ERR#{fingerprint}` | `METADATA` | — | — | `github_issue_number`, `occurrence_count`, `expires_at` (24h TTL) |
