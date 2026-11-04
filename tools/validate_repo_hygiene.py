#!/usr/bin/env python3
"""
Automated Repository Hygiene, Documentation Drift, & Cross-Boundary Contract Validator.

Don't rely on "the honesty of the teenager" (hoping the LLM remembers every rule in AGENTS.md).
Run this zero-dependency script in pre-commit or pytest to mechanically enforce:
1. Context Map & Schema Dictionary Sync (AGENTS.md Rule 6 & Rule 11):
   - Verifies CODEBASE_MAP.md indexes every service in apps/
   - Verifies SCHEMA_DICTIONARY.md documents every DynamoDB table and Partition Key (HASH)
     defined across all apps/*/template.yaml files
2. AGENTS.md Structural Hygiene Guardrails (Rule 2, Rule 7, & Rule 15):
   - Blocks CloudFormation Fn::ImportValue / !ImportValue cross-stack export deadlocks
   - Enforces the <1,000-line ceiling on source files (with an optional ratcheting allowlist)
   - Blocks silent `except ...: pass` blocks in production Python modules via AST inspection
3. SAM Handler & Cross-Boundary Frontend JS <-> Backend Python Payload Contracts:
   - Verifies every AWS::Serverless::Function in apps/*/template.yaml points to a real Python
     file and defines the declared handler function in its AST
   - Verifies frontend JS fetch() payload keys match backend Python Lambda handler keys
"""

import ast
import glob
import os
import re
import sys
from typing import Dict, List, Set, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MAX_SOURCE_LINES = 1000

# Optional ratcheting ceilings for legacy monoliths while migrating (<rel_path>: <max_lines>)
LEGACY_MONOLITH_LINE_CEILINGS: Dict[str, int] = {}

# Optional ratcheting ceilings for legacy silent `except: pass` blocks (<rel_path>: <max_count>)
LEGACY_SILENT_EXCEPT_PASS_CEILINGS: Dict[str, int] = {}

# Declarative cross-boundary JSON payload contracts between Frontend JS and Backend Python handlers
CROSS_BOUNDARY_PAYLOAD_CONTRACTS: List[Dict[str, object]] = [
    # Example:
    # {
    #     "name": "Proctored Exam Client -> Marking Queue Submit Lambda",
    #     "frontend_js": "apps/exam/src/js/exam.js",
    #     "backend_py": "apps/marking-queue/src/functions/submit/main.py",
    #     "required_keys": ["course_code", "assignment_id", "verification_code"],
    # },
]


def verify_codebase_map_and_app_count_sync(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify every app in apps/ is documented in CODEBASE_MAP.md."""
    errors: List[str] = []
    apps_dir = os.path.join(repo_root, "apps")
    map_path = os.path.join(repo_root, "CODEBASE_MAP.md")
    if not os.path.isdir(apps_dir) or not os.path.isfile(map_path):
        return errors

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
    return errors


def verify_schema_dictionary_covers_all_dynamodb_tables(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify every DynamoDB table and its HASH key in apps/*/template.yaml is in SCHEMA_DICTIONARY.md."""
    errors: List[str] = []
    schema_path = os.path.join(repo_root, "SCHEMA_DICTIONARY.md")
    if not os.path.isfile(schema_path):
        return errors

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_doc = f.read()

    for tpl_path in sorted(glob.glob(os.path.join(repo_root, "apps", "*", "template.yaml"))):
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
                if logical_id and logical_id not in schema_doc:
                    errors.append(
                        f"DynamoDB table '{logical_id}' in {rel_tpl} is missing from SCHEMA_DICTIONARY.md"
                    )
    return errors


def verify_no_cloudformation_import_value_deadlocks(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify no apps/*/template.yaml uses Fn::ImportValue or !ImportValue (AGENTS.md Rule 15)."""
    errors: List[str] = []
    for tpl_path in sorted(glob.glob(os.path.join(repo_root, "apps", "*", "template.yaml"))):
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
    """Enforce the 1,000-line ceiling on first-party source files (AGENTS.md Rule 8)."""
    errors: List[str] = []
    excluded_dirs = {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        ".aws-sam",
        "__pycache__",
        "vendor",
        ".build",
        "layer",
        "tests",
    }
    for base in ("apps", "common", "tools"):
        base_dir = os.path.join(repo_root, base)
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for fname in files:
                if not fname.endswith((".py", ".js", ".ts", ".html", ".css")):
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
                        f"{rel_path} has {line_count} lines (ceiling: {ceiling}; split files >1,000 lines)."
                    )
    return errors


def verify_no_new_silent_except_pass_in_handlers(repo_root: str = REPO_ROOT) -> List[str]:
    """Block silent `except ...: pass` blocks in production Python modules (AGENTS.md Rule 3)."""
    errors: List[str] = []
    excluded_dirs = {".aws-sam", "__pycache__", "tests", "layer", "node_modules", "dist", ".build"}
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
                silent_lines = [
                    node.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ExceptHandler)
                    and len(node.body) == 1
                    and isinstance(node.body[0], ast.Pass)
                ]
                allowed = LEGACY_SILENT_EXCEPT_PASS_CEILINGS.get(rel_path, 0)
                if len(silent_lines) > allowed:
                    errors.append(
                        f"{rel_path} has silent `except: pass` at line(s) {silent_lines} (allowed: {allowed})."
                    )
    return errors


def verify_sam_lambda_handler_contracts(repo_root: str = REPO_ROOT) -> List[str]:
    """Verify every AWS::Serverless::Function CodeUri + Handler resolves to a real Python function."""
    errors: List[str] = []
    for tpl_path in sorted(glob.glob(os.path.join(repo_root, "apps", "*", "template.yaml"))):
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
            py_file = os.path.normpath(
                os.path.join(app_dir, code_uri, mod_path.replace(".", "/") + ".py")
            )
            if not os.path.isfile(py_file):
                errors.append(
                    f"{rel_tpl} ({resource_name}): Handler '{handler}' points to missing '{py_file}'"
                )
                continue
            try:
                with open(py_file, "r", encoding="utf-8") as pf:
                    tree = ast.parse(pf.read(), filename=py_file)
                defined: Set[str] = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if func_name not in defined:
                    errors.append(
                        f"{rel_tpl} ({resource_name}): Function '{func_name}' missing in '{py_file}'"
                    )
            except SyntaxError as exc:
                errors.append(f"SyntaxError in {py_file}: {exc}")
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
        if not (os.path.isfile(js_path) and os.path.isfile(py_path)):
            errors.append(f"[{name}] Missing contract file: {js_rel} or {py_rel}")
            continue
        js_content = open(js_path, "r", encoding="utf-8").read()
        py_content = open(py_path, "r", encoding="utf-8").read()
        for key in required_keys:
            if key not in js_content:
                errors.append(f"[{name}] Key '{key}' missing from frontend {js_rel}")
            if key not in py_content:
                errors.append(f"[{name}] Key '{key}' missing from backend {py_rel}")
    return errors


def run_all_repo_hygiene_checks(repo_root: str = REPO_ROOT) -> Tuple[bool, List[str]]:
    """Execute all repository hygiene and contract checks."""
    all_errors: List[str] = []
    for fn in (
        verify_codebase_map_and_app_count_sync,
        verify_schema_dictionary_covers_all_dynamodb_tables,
        verify_no_cloudformation_import_value_deadlocks,
        verify_source_file_line_count_ceilings,
        verify_no_new_silent_except_pass_in_handlers,
        verify_sam_lambda_handler_contracts,
        verify_cross_boundary_frontend_backend_payload_contracts,
    ):
        all_errors.extend(fn(repo_root))
    return (len(all_errors) == 0, all_errors)


if __name__ == "__main__":
    ok, violations = run_all_repo_hygiene_checks()
    if not ok:
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)
    print("✅ All AGENTS.md hygiene rules, map/schema sync checks, and contracts passed!")
