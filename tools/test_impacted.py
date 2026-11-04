#!/usr/bin/env python3
"""
Diff-Aware Impacted Test Runner for the 29-Microservice Monorepo.

Determines the minimal set of pytest suites impacted by current git changes:
- Edits to `apps/<app>/...` -> runs `apps/<app>/tests/`
- Edits to `common/shared/<module>.py` -> scans `apps/*/src/` for consumers of `<module>`
  and runs `apps/<consumer>/tests/` + `tools/tests/test_guardrails.py`
- Edits to `tools/...`, `AGENTS.md`, `CODEBASE_MAP.md`, or `SCHEMA_DICTIONARY.md`
  -> runs `tools/tests/`

Usage:
    python3 tools/test_impacted.py [--dry-run] [--base HEAD]
"""

import argparse
import glob
import os
import subprocess
import sys
from typing import Iterable, List, Set

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_git_changed_files(repo_root: str = REPO_ROOT, base_ref: str = "HEAD") -> List[str]:
    """Return list of modified, staged, and untracked relative file paths from git."""
    changed: Set[str] = set()
    cmds = [
        ["git", "-C", repo_root, "diff", "--name-only", base_ref],
        ["git", "-C", repo_root, "diff", "--name-only", "--cached"],
        ["git", "-C", repo_root, "ls-files", "--others", "--exclude-standard"],
    ]
    for cmd in cmds:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    clean = line.strip()
                    if clean:
                        changed.add(clean)
        except OSError:
            pass
    return sorted(changed)


def find_apps_importing_shared_module(module_name: str, repo_root: str = REPO_ROOT) -> Set[str]:
    """Find all microservices in apps/ whose src/ files reference a shared module name."""
    impacted_apps: Set[str] = set()
    apps_dir = os.path.join(repo_root, "apps")
    if not os.path.isdir(apps_dir):
        return impacted_apps

    for py_path in glob.glob(os.path.join(apps_dir, "*", "src", "**", "*.py"), recursive=True):
        if "/layer/" in py_path or "/.build/" in py_path:
            continue
        try:
            with open(py_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if module_name in content:
                rel = os.path.relpath(py_path, apps_dir)
                app_name = rel.split(os.sep)[0]
                impacted_apps.add(app_name)
        except OSError:
            continue
    return impacted_apps


def resolve_impacted_pytest_targets(
    changed_files: Iterable[str], repo_root: str = REPO_ROOT
) -> List[str]:
    """Map a list of relative changed file paths to existing pytest target directories/files."""
    targets: Set[str] = set()
    impacted_apps: Set[str] = set()

    for rel_path in changed_files:
        norm = rel_path.replace("\\", "/").lstrip("./")
        parts = norm.split("/")

        if parts[0] == "apps" and len(parts) >= 2:
            impacted_apps.add(parts[1])
            if parts[-1] in ("template.yaml", "data.yaml"):
                guardrails = os.path.join(repo_root, "tools", "tests", "test_guardrails.py")
                if os.path.isfile(guardrails):
                    targets.add("tools/tests/test_guardrails.py")

        elif parts[0] == "common" and norm.endswith(".py"):
            mod_name = os.path.splitext(os.path.basename(norm))[0]
            impacted_apps.update(find_apps_importing_shared_module(mod_name, repo_root))
            guardrails = os.path.join(repo_root, "tools", "tests", "test_guardrails.py")
            if os.path.isfile(guardrails):
                targets.add("tools/tests/test_guardrails.py")

        elif parts[0] in ("tools", "agents") or norm in (
            "AGENTS.md",
            "CODEBASE_MAP.md",
            "SCHEMA_DICTIONARY.md",
        ):
            guardrails = os.path.join(repo_root, "tools", "tests", "test_guardrails.py")
            if os.path.isfile(guardrails):
                targets.add("tools/tests/test_guardrails.py")
            if "builder" in norm or "slides" in norm:
                builder_test = os.path.join(repo_root, "tools", "tests", "test_builder.py")
                if os.path.isfile(builder_test):
                    targets.add("tools/tests/test_builder.py")

    for app in sorted(impacted_apps):
        app_tests = os.path.join(repo_root, "apps", app, "tests")
        if os.path.isdir(app_tests):
            targets.add(f"apps/{app}/tests")

    return sorted(targets)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run pytest only on microservice suites impacted by current git changes."
    )
    parser.add_argument("--base", default="HEAD", help="Git ref to diff against (default: HEAD).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print impacted pytest targets without executing pytest.",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        help="Explicit list of changed files (overrides git diff).",
    )
    args = parser.parse_args()

    changed = args.files if args.files is not None else get_git_changed_files(REPO_ROOT, args.base)
    targets = resolve_impacted_pytest_targets(changed, REPO_ROOT)

    if not targets:
        print("✅ No impacted test suites detected.")
        return

    print(f"🎯 Impacted pytest targets ({len(targets)}):")
    for t in targets:
        print(f"  - {t}")

    if args.dry_run:
        return

    pytest_bin = os.path.join(REPO_ROOT, ".venv", "bin", "pytest")
    cmd = [pytest_bin if os.path.isfile(pytest_bin) else sys.executable, "-m", "pytest"]
    if os.path.isfile(pytest_bin):
        cmd = [pytest_bin]
    cmd.extend(targets)

    res = subprocess.run(cmd, cwd=REPO_ROOT)
    sys.exit(res.returncode)


if __name__ == "__main__":
    main()
