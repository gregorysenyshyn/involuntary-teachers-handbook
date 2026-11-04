#!/usr/bin/env python3
"""
Automated Repository Hygiene, Documentation Drift, & Cross-Boundary Contract Validator.

Mechanically enforces key rules from AGENTS.md so compliance does not rely on LLM memory:
1. Context Map & Schema Dictionary Drift Detection (Rule 6 & Rule 11):
   - Verifies CODEBASE_MAP.md indexes every app in apps/ and matches the declared app count.
   - Verifies SCHEMA_DICTIONARY.md documents every DynamoDB table and Partition Key (HASH)
     defined across all apps/*/template.yaml files.
2. AGENTS.md Structural Hygiene Guardrails (Rule 2, Rule 7, & Rule 15):
   - Blocks CloudFormation Fn::ImportValue / !ImportValue cross-stack export deadlocks.
   - Enforces the 1,000-line ceiling on first-party source files (with a ratcheting ceiling
     for legacy monoliths so no new file can cross 1,000 lines and legacy files cannot grow).
   - Blocks new silent `except ...: pass` blocks in production Lambda handlers and shared modules.
3. Cross-Boundary Frontend JS <-> Backend Python & SAM Handler Contracts:
   - Verifies every AWS::Serverless::Function in apps/*/template.yaml resolves to an existing
     Python module and defines the declared callable handler function in its AST.
   - Verifies critical frontend JS fetch() payload keys match backend Python Lambda handler keys.
"""

import ast
import glob
import os
import re
import sys
from typing import Dict, List, Set, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Default line limit for all first-party source files (AGENTS.md Rule 7)
DEFAULT_MAX_SOURCE_LINES = 1000

# Ratcheting ceiling for pre-existing >1,000-line files.
# Existing files may shrink (and should be removed from this dict once <=1,000 lines),
# but may NEVER grow past their recorded ceiling, and no new file may exceed 1,000 lines.
LEGACY_MONOLITH_LINE_CEILINGS: Dict[str, int] = {
    "apps/asyncacademy/src/js/account-students.js": 1240,
    "apps/asyncacademy/src/partials/account-form.html": 1234,
    "apps/english/src/js/scripts.js": 1702,
    "apps/exam/src/js/exam.js": 1529,
    "apps/gregorysenyshyn/src/js/teleprompter.js": 1379,
    "apps/history-simulator/src/js/dashboard.js": 4485,
    "apps/history-simulator/src/pages/index.html": 1052,
    "apps/music/src/js/scripts.js": 3155,
    "apps/ontarioliteracy/src/js/dashboard.js": 1032,
    "apps/ontarioliteracy/src/pages/main/olc4o-orientation.html": 1364,
    "apps/ontarioliteracy/src/pages/main/olc4o-workspace.html": 2778,
    "apps/phys-ed/src/js/scripts.js": 3119,
    "apps/phys-ed/src/pages/index.html": 1574,
    "apps/top-secret-area/src/js/marking-dashboard.js": 2258,
    "apps/top-secret-area/src/js/students-admin.js": 1652,
    "apps/top-secret-area/src/pages/emails.html": 1032,
    "apps/top-secret-area/src/pages/marking-dashboard.html": 1291,
    "apps/top-secret-area/src/pages/students.html": 1305,
    "apps/violet/src/styles.css": 1061,
    "apps/yogaanarchy/src/pages/diagnostic.html": 1189,
    "apps/yogaanarchy/src/pages/poselab.html": 2220,
    "apps/yogaanarchy/src/pages/timeline.html": 1909,
}

# Zero-tolerance for silent `except ...: pass` blocks in production Python modules (AGENTS.md Rule 2).
LEGACY_SILENT_EXCEPT_PASS_CEILINGS: Dict[str, int] = {}

# Declarative cross-boundary JSON payload contracts between Frontend JS and Backend Python handlers
CROSS_BOUNDARY_PAYLOAD_CONTRACTS: List[Dict[str, object]] = [
    {
        "name": "Portfolio Contact Form -> Email Contact Lambda",
        "frontend_js": "apps/gregorysenyshyn/src/js/contact.js",
        "backend_py": "apps/email/src/functions/contact_form/main.py",
        "required_keys": ["name", "email", "phone", "message", "token"],
    },
    {
        "name": "Brimer Racer Timer -> Brimer API Lambda",
        "frontend_js": "apps/gregorysenyshyn/src/js/brimer.js",
        "backend_py": "apps/gregorysenyshyn/src/lambda/brimer_api.py",
        "required_keys": ["boat_name", "finish_time_utc", "elapsed_seconds", "track_points"],
    },
    {
        "name": "Proctored Exam Client -> Marking Queue Submit Lambda",
        "frontend_js": "apps/exam/src/js/exam.js",
        "backend_py": "apps/marking-queue/src/functions/admin/marking_queue_submit/actions/submit.py",
        "required_keys": [
            "course_code",
            "assignment_id",
            "assignment_name",
            "proctoring_sessions",
            "verification_code",
        ],
    },
    {
        "name": "Violet Classroom Hub -> Violet Jeux API Lambda",
        "frontend_js": "apps/violet/src/jeux.js",
        "backend_py": "apps/violet/src/backend/jeux_api.py",
        "required_keys": ["class_id", "vocab_words", "vocab_mode", "personal_vocab_words"],
    },
]


def verify_codebase_map_and_app_count_sync(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify every app in apps/ is in CODEBASE_MAP.md and microservice counts match."""
    errors: List[str] = []
    apps_dir = os.path.join(repo_root, "apps")
    map_path = os.path.join(repo_root, "CODEBASE_MAP.md")
    schema_path = os.path.join(repo_root, "SCHEMA_DICTIONARY.md")

    if not os.path.isdir(apps_dir) or not os.path.isfile(map_path):
        return ["Missing apps/ directory or CODEBASE_MAP.md"]

    actual_apps = sorted(
        d
        for d in os.listdir(apps_dir)
        if os.path.isdir(os.path.join(apps_dir, d)) and not d.startswith(".")
    )
    with open(map_path, "r", encoding="utf-8") as f:
        map_content = f.read()

    for app in actual_apps:
        patterns = [f"apps/{app}/", f"apps/{app}`", f"`apps/{app}`", f"apps/{app} "]
        if not any(p in map_content for p in patterns):
            errors.append(f"App 'apps/{app}' is missing from CODEBASE_MAP.md")

    count_match = re.search(r"\*\*(\d+)\s+active\s+microservices", map_content)
    if count_match and int(count_match.group(1)) != len(actual_apps):
        errors.append(
            f"CODEBASE_MAP.md header declares {count_match.group(1)} microservices, "
            f"but apps/ contains {len(actual_apps)} directories."
        )

    if os.path.isfile(schema_path):
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_content = f.read()
        schema_count = re.search(r"across\s+the\s+(\d+)\s+microservices", schema_content)
        if schema_count and int(schema_count.group(1)) != len(actual_apps):
            errors.append(
                f"SCHEMA_DICTIONARY.md header declares {schema_count.group(1)} microservices, "
                f"but apps/ contains {len(actual_apps)} directories."
            )

    return errors


def verify_schema_dictionary_covers_all_dynamodb_tables(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify every DynamoDB table and its HASH key in apps/*/template.yaml is in SCHEMA_DICTIONARY.md."""
    errors: List[str] = []
    schema_path = os.path.join(repo_root, "SCHEMA_DICTIONARY.md")
    if not os.path.isfile(schema_path):
        return ["Missing SCHEMA_DICTIONARY.md"]

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_doc = f.read()

    template_paths = sorted(glob.glob(os.path.join(repo_root, "apps", "*", "template.yaml")))
    for tpl_path in template_paths:
        rel_tpl = os.path.relpath(tpl_path, repo_root)
        with open(tpl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            if "AWS::DynamoDB::Table" in line or "AWS::Serverless::SimpleTable" in line:
                logical_id = None
                for j in range(i - 1, max(0, i - 10), -1):
                    m = re.match(r"^  ([A-Za-z0-9_]+):\s*$", lines[j])
                    if m:
                        logical_id = m.group(1)
                        break
                if not logical_id:
                    continue

                if logical_id not in schema_doc:
                    errors.append(
                        f"DynamoDB table '{logical_id}' in {rel_tpl} is missing from SCHEMA_DICTIONARY.md"
                    )
                    continue

                # Also extract the table's primary Partition Key (HASH) and confirm it appears in SCHEMA_DICTIONARY.md
                block_lines: List[str] = []
                for k in range(i + 1, min(len(lines), i + 50)):
                    if re.match(r"^  [A-Za-z0-9_]+:\s*$", lines[k]):
                        break
                    block_lines.append(lines[k])
                block_str = "".join(block_lines)
                hash_keys = re.findall(
                    r"AttributeName:\s*([A-Za-z0-9_]+)\s*\n\s*KeyType:\s*HASH", block_str
                )
                if hash_keys:
                    primary_hash_key = hash_keys[0]
                    if primary_hash_key not in schema_doc:
                        errors.append(
                            f"Partition Key '{primary_hash_key}' for table '{logical_id}' ({rel_tpl}) "
                            "is not documented in SCHEMA_DICTIONARY.md"
                        )

    return errors


def verify_no_cloudformation_import_value_deadlocks(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify no apps/*/template.yaml uses Fn::ImportValue or !ImportValue (AGENTS.md Rule 15)."""
    errors: List[str] = []
    template_paths = sorted(glob.glob(os.path.join(repo_root, "apps", "*", "template.yaml")))
    for tpl_path in template_paths:
        rel_tpl = os.path.relpath(tpl_path, repo_root)
        with open(tpl_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if "ImportValue" in line and not line.lstrip().startswith("#"):
                    errors.append(
                        f"{rel_tpl}:{line_no} uses CloudFormation ImportValue. "
                        "Use SSM Parameter Store decoupling instead."
                    )
    return errors


def verify_source_file_line_count_ceilings(repo_root: str = REPO_ROOT) -> List[str]:
    """Enforce the 1,000-line ceiling on first-party source files with ratcheting for legacy files."""
    errors: List[str] = []
    excluded_dirs = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        ".aws-sam",
        "__pycache__",
        "vendor",
        "reveal",
        ".build",
        "layer",
        "tests",
    }

    for base in ("apps", "common", "tools", "agents"):
        base_dir = os.path.join(repo_root, base)
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for fname in files:
                if not fname.endswith((".py", ".js", ".html", ".css")):
                    continue
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, repo_root).replace("\\", "/")
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                        line_count = sum(1 for _ in f)
                except OSError:
                    continue

                ceiling = LEGACY_MONOLITH_LINE_CEILINGS.get(rel_path, DEFAULT_MAX_SOURCE_LINES)
                if line_count > ceiling:
                    errors.append(
                        f"{rel_path} has {line_count} lines (exceeds ceiling of {ceiling} lines; "
                        "AGENTS.md Rule 7 requires splitting monolithic files >1,000 lines)."
                    )
    return errors


def verify_no_new_silent_except_pass_in_handlers(repo_root: str = REPO_ROOT) -> List[str]:
    """Block new silent `except ...: pass` blocks in production Python modules (AGENTS.md Rule 2)."""
    errors: List[str] = []
    excluded_dirs = {
        ".aws-sam",
        "__pycache__",
        "tests",
        "layer",
        "node_modules",
        "dist",
        "vendor",
        ".build",
        "scripts",
    }

    for base in ("apps", "common"):
        base_dir = os.path.join(repo_root, base)
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, repo_root).replace("\\", "/")
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=rel_path)
                except (OSError, SyntaxError):
                    continue

                silent_lines: List[int] = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ExceptHandler):
                        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                            silent_lines.append(node.lineno)

                allowed_count = LEGACY_SILENT_EXCEPT_PASS_CEILINGS.get(rel_path, 0)
                if len(silent_lines) > allowed_count:
                    errors.append(
                        f"{rel_path} contains {len(silent_lines)} silent `except: pass` block(s) "
                        f"at line(s) {silent_lines} (allowed ceiling: {allowed_count}). "
                        "Fail fast or log descriptive exceptions per AGENTS.md Rule 2."
                    )
    return errors


def verify_sam_lambda_handler_contracts(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify every AWS::Serverless::Function CodeUri + Handler resolves to a real Python function."""
    errors: List[str] = []
    template_paths = sorted(glob.glob(os.path.join(repo_root, "apps", "*", "template.yaml")))
    for tpl_path in template_paths:
        app_dir = os.path.dirname(tpl_path)
        rel_tpl = os.path.relpath(tpl_path, repo_root)
        with open(tpl_path, "r", encoding="utf-8") as f:
            content = f.read()

        for block in re.split(r"\n  (?=[A-Za-z0-9_]+:\s*\n)", content):
            if "AWS::Serverless::Function" not in block:
                continue
            resource_name = block.split(":")[0].strip()
            cu_match = re.search(r"CodeUri:\s*([^\s#]+)", block)
            hd_match = re.search(r"Handler:\s*([^\s#]+)", block)
            if not (cu_match and hd_match):
                continue

            code_uri = cu_match.group(1).strip("'\"")
            handler = hd_match.group(1).strip("'\"")
            if "." not in handler:
                continue
            mod_path, func_name = handler.rsplit(".", 1)
            rel_mod = mod_path.replace(".", "/") + ".py"

            # Resolve standard CodeUri or Makefile-staged source roots (src/functions, src/backend)
            snake_res = re.sub(r"(?<!^)(?=[A-Z])", "_", resource_name.removesuffix("Function")).lower()
            candidate_paths = [
                os.path.normpath(os.path.join(app_dir, code_uri, rel_mod)),
                os.path.normpath(os.path.join(app_dir, "src", "functions", rel_mod)),
                os.path.normpath(os.path.join(app_dir, "src", "backend", rel_mod)),
                os.path.normpath(os.path.join(app_dir, "src", "functions", snake_res, rel_mod)),
            ]
            py_file = next((p for p in candidate_paths if os.path.isfile(p)), candidate_paths[0])
            if not os.path.isfile(py_file):
                errors.append(
                    f"{rel_tpl} ({resource_name}): Handler '{handler}' points to missing file "
                    f"'{os.path.relpath(py_file, repo_root)}'"
                )
                continue

            try:
                with open(py_file, "r", encoding="utf-8") as pf:
                    tree = ast.parse(pf.read(), filename=py_file)
                defined_names: Set[str] = set()
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defined_names.add(node.name)
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                defined_names.add(target.id)
                    elif isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            defined_names.add(alias.asname or alias.name)
                if func_name not in defined_names:
                    errors.append(
                        f"{rel_tpl} ({resource_name}): Function '{func_name}' not found in "
                        f"'{os.path.relpath(py_file, repo_root)}'"
                    )
            except SyntaxError as exc:
                errors.append(f"SyntaxError in {os.path.relpath(py_file, repo_root)}: {exc}")

    return errors


def verify_cross_boundary_frontend_backend_payload_contracts(
    repo_root: str = REPO_ROOT,
) -> List[str]:
    """Verify declared frontend JS fetch() payload keys match backend Python handler keys."""
    errors: List[str] = []
    for contract in CROSS_BOUNDARY_PAYLOAD_CONTRACTS:
        name = str(contract["name"])
        js_rel = str(contract["frontend_js"])
        py_rel = str(contract["backend_py"])
        required_keys = list(contract["required_keys"])  # type: ignore[arg-type]

        js_path = os.path.join(repo_root, js_rel)
        py_path = os.path.join(repo_root, py_rel)
        if not os.path.isfile(js_path):
            errors.append(f"[{name}] Missing frontend file: {js_rel}")
            continue
        if not os.path.isfile(py_path):
            errors.append(f"[{name}] Missing backend file: {py_rel}")
            continue

        with open(js_path, "r", encoding="utf-8") as jf:
            js_content = jf.read()
        with open(py_path, "r", encoding="utf-8") as pf:
            py_content = pf.read()

        for key in required_keys:
            if key not in js_content:
                errors.append(
                    f"[{name}] Payload key '{key}' missing from frontend caller {js_rel}"
                )
            if key not in py_content:
                errors.append(
                    f"[{name}] Payload key '{key}' missing from backend handler {py_rel}"
                )

    return errors


def verify_no_unmasked_pii_in_logs(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify no production Python module logs raw PII variables in f-strings without mask_*() (Rule 1)."""
    errors: List[str] = []
    pii_variable_names = {
        "email",
        "student_email",
        "parent_email",
        "invitee_email",
        "phone",
        "oen",
    }
    log_methods = {"info", "warning", "error", "debug", "critical", "exception"}
    excluded_dirs = {
        ".aws-sam",
        "__pycache__",
        "tests",
        "layer",
        "node_modules",
        "dist",
        "vendor",
        ".build",
        "scripts",
    }

    for base in ("apps", "common"):
        base_dir = os.path.join(repo_root, base)
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                abs_path = os.path.join(root, fname)
                rel_path = os.path.relpath(abs_path, repo_root).replace("\\", "/")
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=rel_path)
                except (OSError, SyntaxError):
                    continue

                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    is_log_call = (
                        isinstance(node.func, ast.Name) and node.func.id == "print"
                    ) or (
                        isinstance(node.func, ast.Attribute) and node.func.attr in log_methods
                    )
                    if not is_log_call:
                        continue

                    for arg in node.args:
                        for sub in ast.walk(arg):
                            if isinstance(sub, ast.FormattedValue) and isinstance(
                                sub.value, ast.Name
                            ):
                                if sub.value.id in pii_variable_names:
                                    errors.append(
                                        f"{rel_path}:{node.lineno} interpolates raw PII variable "
                                        f"'{sub.value.id}' in log/print f-string without mask_email()/mask_phone() "
                                        "(violates AGENTS.md Rule 1 / PII_SECURITY.md)."
                                    )
    return errors


def _normalize_ssm_parameter_path(raw_path: str) -> str:
    """Normalize CloudFormation !Sub or environment-specific SSM paths for producer/consumer matching."""
    cleaned = re.sub(r"^!Sub\s+", "", raw_path.strip()).strip("'\"")
    cleaned = re.sub(r"\$\{ProjectName\}|\$\{Project\}", "asyncacademy", cleaned)
    cleaned = re.sub(r"\$\{Environment\}|\$\{Env\}", "{env}", cleaned)
    cleaned = re.sub(
        r"^/(asyncacademy|ontarioliteracy|personal)/(dev|prod|personal)/",
        r"/\1/{env}/",
        cleaned,
    )
    return cleaned


def verify_cross_stack_ssm_parameter_wiring(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify cross-stack SSM paths consumed in apps/*/data.yaml match exported AWS::SSM::Parameter resources."""
    errors: List[str] = []
    try:
        import yaml
    except ImportError:
        return errors

    template_paths = sorted(glob.glob(os.path.join(repo_root, "apps", "*", "template.yaml")))
    active_apps = {os.path.basename(os.path.dirname(p)) for p in template_paths}

    exported_ssm_paths: Set[str] = set()
    for tpl_path in template_paths:
        with open(tpl_path, "r", encoding="utf-8") as f:
            content = f.read()
        for block in re.split(r"\n  (?=[A-Za-z0-9_]+:\s*\n)", content):
            if "Type: AWS::SSM::Parameter" in block:
                for m in re.finditer(r"^\s+Name:\s*(.+)$", block, re.MULTILINE):
                    exported_ssm_paths.add(_normalize_ssm_parameter_path(m.group(1)))

    for data_path in sorted(glob.glob(os.path.join(repo_root, "apps", "*", "data.yaml"))):
        consumer_app = os.path.basename(os.path.dirname(data_path))
        rel_data = os.path.relpath(data_path, repo_root)
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            continue

        for env_name, env_cfg in (data.get("environments") or {}).items():
            for param_key, param_val in (env_cfg.get("parameters") or {}).items():
                if not (isinstance(param_val, str) and param_val.startswith("/asyncacademy/")):
                    continue
                norm_path = _normalize_ssm_parameter_path(param_val)
                parts = norm_path.strip("/").split("/")
                # Check cross-stack references targeting another microservice in apps/
                if len(parts) == 4 and parts[2] in active_apps and parts[2] != consumer_app:
                    if norm_path not in exported_ssm_paths:
                        errors.append(
                            f"{rel_data} ({env_name}.{param_key}) references cross-stack SSM parameter "
                            f"'{param_val}', which is not exported by 'apps/{parts[2]}/template.yaml'."
                        )
    return errors


def run_all_repo_hygiene_checks(repo_root: str = REPO_ROOT) -> Tuple[bool, List[str]]:
    """Run all repository hygiene, documentation sync, and cross-boundary contract checks."""
    all_errors: List[str] = []
    checks = [
        verify_codebase_map_and_app_count_sync,
        verify_schema_dictionary_covers_all_dynamodb_tables,
        verify_no_cloudformation_import_value_deadlocks,
        verify_source_file_line_count_ceilings,
        verify_no_new_silent_except_pass_in_handlers,
        verify_no_unmasked_pii_in_logs,
        verify_cross_stack_ssm_parameter_wiring,
        verify_sam_lambda_handler_contracts,
        verify_cross_boundary_frontend_backend_payload_contracts,
    ]
    for check_fn in checks:
        all_errors.extend(check_fn(repo_root))
    return (len(all_errors) == 0, all_errors)


if __name__ == "__main__":
    ok, violations = run_all_repo_hygiene_checks()
    if not ok:
        print("❌ Repository Hygiene & Contract Guardrail Violations:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    print(
        "✅ All AGENTS.md hygiene rules, CODEBASE_MAP/SCHEMA_DICTIONARY sync checks, "
        "and cross-boundary contracts passed!"
    )
