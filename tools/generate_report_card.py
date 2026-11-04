#!/usr/bin/env python3
"""
The Codebase Report Card Generator (Agent Readiness & Cloud Hygiene Auditor).

Grades any software repository from A+ to F across 6 objective "Involuntary Teacher" rubrics:
1. World-Model & Syllabus Legibility (AGENTS.md, CODEBASE_MAP.md, SCHEMA_DICTIONARY.md, ADRs)
2. Context Window Tax (Monolithic files >1,000 lines & estimated wasted tokens per agent turn)
3. AST Comprehensibility (Semantically-dense function naming & Python type-hint coverage)
4. Silent Failure Risk (Silent `except: pass` in Python & empty `catch () {}` in JS/TS)
5. Self-Assessment Rubric (Test-to-source module ratio & assertion density)
6. Blast Radius & Cloud Cost Guardrails (Fn::ImportValue deadlocks & hardcoded IAM ARNs)

Usage:
    python3 tools/generate_report_card.py [path/to/repo] [--output report_card.md]
"""

import argparse
import ast
from collections import defaultdict
import copy
import hashlib
import os
import re
import sys
from typing import Dict, List, Tuple


class _AstSkeletonNormalizer(ast.NodeTransformer):
    """Strip docstrings and normalize variable/arg names and literals to detect structural AST clones."""

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        return ast.FunctionDef(
            name="_FUNC_",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg="_a") for _ in node.args.args],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            body=[self.visit(b) for b in body],
            decorator_list=[],
            returns=None,
        )

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.Name(id="_V_", ctx=node.ctx)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.Constant(value=type(node.value).__name__)

VAGUE_FUNCTION_NAMES = {
    "process",
    "handle",
    "handler",
    "run",
    "execute",
    "do_it",
    "helper",
    "util",
    "utils",
    "parse",
    "update",
    "check",
    "calc",
    "calculate",
    "work",
    "step",
    "func",
    "fn",
    "cb",
    "callback",
    "temp",
    "test",
}

PROTOCOL_OR_ENTRYPOINT_NAMES = {
    "main",
    "default",
    "cli",
    "filter",
    "decorator",
    "wrapper",
    "constructor",
    "representer",
}

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".aws-sam",
    "__pycache__",
    "vendor",
    "reveal",
    ".build",
    "layer",
    "coverage",
    ".next",
    "out",
    "scratch",
    "graphify-out",
    "examples",
}


def score_to_letter_grade(score: float) -> str:
    """Convert a 0-100 numerical score into an academic letter grade."""
    if score >= 95:
        return "A+"
    if score >= 90:
        return "A"
    if score >= 85:
        return "A-"
    if score >= 80:
        return "B+"
    if score >= 75:
        return "B"
    if score >= 70:
        return "B-"
    if score >= 65:
        return "C+"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def audit_repository_and_generate_report_card(repo_path: str) -> Tuple[str, Dict[str, object]]:
    """Scan a target repository and return a formatted Markdown Report Card and metrics dict."""
    repo_root = os.path.abspath(repo_path)
    repo_name = os.path.basename(repo_root)

    # 1. World-Model & Syllabus Legibility
    has_agents_md = any(
        os.path.isfile(os.path.join(repo_root, p))
        for p in ("AGENTS.md", "CLAUDE.md", ".cursorrules", ".github/copilot-instructions.md")
    )
    has_codebase_map = os.path.isfile(os.path.join(repo_root, "CODEBASE_MAP.md"))
    has_schema_dict = os.path.isfile(os.path.join(repo_root, "SCHEMA_DICTIONARY.md"))
    has_pack_context = os.path.isfile(os.path.join(repo_root, "tools", "pack_context.py"))
    adr_dir = os.path.join(repo_root, "docs", "adr")
    adr_count = (
        len([f for f in os.listdir(adr_dir) if f.endswith(".md")])
        if os.path.isdir(adr_dir)
        else 0
    )

    world_model_score = (
        (35 if has_agents_md else 0)
        + (25 if has_codebase_map else 0)
        + (20 if has_schema_dict else 0)
        + (10 if has_pack_context else 0)
        + (10 if adr_count > 0 else 0)
    )

    # Walk repository files
    source_files: List[Tuple[int, int, str]] = []  # (lines, chars, rel_path)
    test_files: List[str] = []
    total_assertions = 0
    unspecced_test_patches = 0
    total_py_functions = 0
    typed_py_functions = 0
    semantically_dense_functions = 0
    vague_functions: List[str] = []
    silent_py_excepts: List[str] = []
    silent_js_catches: List[str] = []
    cfn_import_values: List[str] = []
    hardcoded_iam_arns: List[str] = []
    raw_prod_prints: List[str] = []
    structured_prod_logs = 0
    missing_http_timeouts: List[str] = []
    uncached_boto3_calls: List[str] = []
    ast_clone_buckets: Dict[str, List[Tuple[int, str]]] = defaultdict(list)

    empty_js_catch_re = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")
    iam_arn_re = re.compile(r"arn:aws:iam::\d{12}:role/")
    http_fns = {
        "urlopen",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.patch",
        "requests.request",
    }
    log_methods = {"info", "warning", "error", "debug", "critical", "exception"}

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for fname in files:
            abs_path = os.path.join(root, fname)
            rel_path = os.path.relpath(abs_path, repo_root).replace("\\", "/")

            # Check IaC / config files
            if fname in ("template.yaml", "template.yml", "serverless.yml", "data.yaml"):
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                        iac_text = f.read()
                    if "ImportValue" in iac_text and fname.startswith("template"):
                        cfn_import_values.append(rel_path)
                    if fname == "data.yaml" and iam_arn_re.search(iac_text):
                        hardcoded_iam_arns.append(rel_path)
                except OSError:
                    pass

            if not fname.endswith((".py", ".js", ".ts", ".tsx", ".html", ".css")) or fname.endswith(
                (".min.js", ".min.css")
            ):
                continue

            is_test = (
                "/tests/" in f"/{rel_path}"
                or fname.startswith("test_")
                or fname.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts"))
                or rel_path.startswith("common/testing/")
            )
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue

            line_count = content.count("\n") + 1
            if is_test:
                if not rel_path.startswith("common/testing/"):
                    test_files.append(rel_path)
                    total_assertions += len(re.findall(r"\b(?:assert|expect)\b", content))
                    if fname.endswith(".py") and "strict_patch" not in content:
                        try:
                            test_tree = ast.parse(content, filename=rel_path)
                            for tnode in ast.walk(test_tree):
                                if isinstance(tnode, ast.Call):
                                    fn_str = ast.unparse(tnode.func)
                                    if fn_str in ("patch", "unittest.mock.patch", "mock.patch"):
                                        kw_names = {k.arg for k in tnode.keywords if k.arg}
                                        if len(tnode.args) == 1 and not (
                                            kw_names
                                            & {"autospec", "spec", "spec_set", "new", "new_callable"}
                                        ):
                                            unspecced_test_patches += 1
                        except SyntaxError:
                            pass
                continue

            source_files.append((line_count, len(content), rel_path))

            is_prod_module = rel_path.startswith(("apps/", "common/")) and "/scripts/" not in f"/{rel_path}"
            is_prod_lambda_or_shared = (
                (rel_path.startswith("common/shared/") or (rel_path.startswith("apps/") and "/src/" in f"/{rel_path}"))
                and "/scripts/" not in f"/{rel_path}"
            )

            if is_prod_module and fname.endswith((".js", ".ts", ".tsx")):
                for m in empty_js_catch_re.finditer(content):
                    line_no = content[: m.start()].count("\n") + 1
                    silent_js_catches.append(f"{rel_path}:{line_no}")

            if fname.endswith(".py"):
                try:
                    tree = ast.parse(content, filename=rel_path)
                except SyntaxError:
                    continue

                for node in ast.walk(tree):
                    if is_prod_lambda_or_shared and isinstance(node, ast.Call):
                        fn_str = ast.unparse(node.func)
                        if isinstance(node.func, ast.Name) and node.func.id == "print":
                            raw_prod_prints.append(f"{rel_path}:{node.lineno}")
                        elif isinstance(node.func, ast.Attribute) and node.func.attr in log_methods:
                            structured_prod_logs += 1
                        if fn_str in http_fns:
                            kw_names = {k.arg for k in node.keywords if k.arg}
                            if "timeout" not in kw_names and len(node.args) < 3:
                                missing_http_timeouts.append(f"{rel_path}:{node.lineno} (`{fn_str}`)")

                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if is_prod_lambda_or_shared:
                            has_global = any(isinstance(n, ast.Global) for n in ast.walk(node))
                            has_cache_dec = any("cache" in ast.unparse(d) for d in node.decorator_list)
                            if not (
                                has_global
                                or has_cache_dec
                                or node.name.startswith(("get_", "_get_", "__init__"))
                            ):
                                for sub in ast.walk(node):
                                    if isinstance(sub, ast.Call) and ast.unparse(sub.func) in (
                                        "boto3.client",
                                        "boto3.resource",
                                    ):
                                        uncached_boto3_calls.append(
                                            f"{rel_path}:{sub.lineno} (`{node.name}`)"
                                        )
                        if node.name.startswith("__") and node.name.endswith("__"):
                            continue
                        total_py_functions += 1
                        has_type = node.returns is not None or any(
                            a.annotation is not None for a in node.args.args
                        )
                        if has_type:
                            typed_py_functions += 1

                        clean_name = node.name.strip("_")
                        arg_names = [a.arg for a in node.args.args]
                        is_property = any(
                            isinstance(d, ast.Name) and d.id == "property"
                            for d in node.decorator_list
                        )
                        is_lambda_entrypoint = clean_name == "handler" and arg_names[:2] == [
                            "event",
                            "context",
                        ]
                        if (
                            clean_name in PROTOCOL_OR_ENTRYPOINT_NAMES
                            or is_property
                            or is_lambda_entrypoint
                        ):
                            semantically_dense_functions += 1
                        elif clean_name.lower() in VAGUE_FUNCTION_NAMES:
                            if len(vague_functions) < 10:
                                vague_functions.append(f"{rel_path}:{node.lineno} (`{node.name}`)")
                        elif "_" in clean_name or len(clean_name) >= 12:
                            semantically_dense_functions += 1

                        if isinstance(node, ast.FunctionDef):
                            fn_lines = (node.end_lineno or node.lineno) - node.lineno + 1
                            if fn_lines >= 12 and "/scripts/" not in f"/{rel_path}":
                                norm_tree = _AstSkeletonNormalizer().visit(copy.deepcopy(node))
                                stmt_count = sum(
                                    1 for n in ast.walk(norm_tree) if isinstance(n, ast.stmt)
                                )
                                if stmt_count >= 4:
                                    digest = hashlib.sha256(
                                        ast.dump(norm_tree, include_attributes=False).encode("utf-8")
                                    ).hexdigest()[:10]
                                    ast_clone_buckets[digest].append(
                                        (fn_lines, f"{rel_path}:{node.lineno} (`{node.name}`)")
                                    )

                    elif is_prod_module and isinstance(node, ast.ExceptHandler):
                        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                            silent_py_excepts.append(f"{rel_path}:{node.lineno}")

    # 2. Context Window Tax Score
    monoliths_over_1000 = [x for x in source_files if x[0] > 1000]
    wasted_tokens_per_turn = sum(max(0, (chars - 20000) // 4) for _, chars, _ in monoliths_over_1000)
    total_src = max(1, len(source_files))
    monolith_ratio = len(monoliths_over_1000) / total_src
    context_score = max(0.0, min(100.0, 100.0 - (monolith_ratio * 350.0)))

    # 3. AST Comprehensibility Score
    if total_py_functions > 0:
        dense_pct = (semantically_dense_functions / total_py_functions) * 100.0
        type_pct = (typed_py_functions / total_py_functions) * 100.0
        comprehensibility_score = min(100.0, (dense_pct * 0.65) + (type_pct * 0.35) + 15.0)
    else:
        dense_pct, type_pct, comprehensibility_score = 75.0, 50.0, 75.0

    # 4. Silent Failure Risk Score
    total_silent = len(silent_py_excepts) + len(silent_js_catches)
    silent_per_100_files = (total_silent / total_src) * 100.0
    silent_score = max(0.0, min(100.0, 100.0 - (silent_per_100_files * 3.5)))

    # 5. Self-Assessment & Mock Fidelity Score (measured against non-stub executable modules)
    executable_files = [
        x
        for x in source_files
        if x[2].endswith((".py", ".js", ".ts", ".tsx"))
        and not x[2].endswith("__init__.py")
        and "/scripts/" not in f"/{x[2]}"
    ]
    total_exec = max(1, len(executable_files))
    test_ratio = len(test_files) / total_exec
    assertion_bonus = min(25.0, (total_assertions / total_exec) * 3.0)
    has_moto_harness = os.path.isfile(os.path.join(repo_root, "common", "testing", "sam_moto_harness.py"))
    has_property_fuzzing = any("properties" in tf for tf in test_files)
    sophistication_bonus = (10.0 if has_moto_harness else 0.0) + (8.0 if has_property_fuzzing else 0.0)
    raw_testing_score = min(100.0, (test_ratio * 180.0) + assertion_bonus + sophistication_bonus)
    testing_score = max(0.0, raw_testing_score - (unspecced_test_patches * 1.5))

    # 6. Blast Radius & Cloud Cost Guardrail Score
    iac_penalties = (len(cfn_import_values) * 20.0) + (len(hardcoded_iam_arns) * 25.0)
    iac_score = max(0.0, 100.0 - iac_penalties)

    # 7. Structural AST Clone Hygiene (Copy-Pasted Control-Flow Skeletons >= 12 lines)
    clone_clusters = [
        (sum(item[0] for item in items), digest, items)
        for digest, items in ast_clone_buckets.items()
        if len(items) > 1
    ]
    clone_clusters.sort(reverse=True)
    duplicated_ast_lines = sum(total_l for total_l, _, _ in clone_clusters)
    clone_score = max(0.0, min(100.0, 100.0 - (len(clone_clusters) * 4.0)))

    # 8. Observability & PII-Safe Logging (Structured logger.* vs raw print() bypasses)
    observability_score = max(0.0, min(100.0, 100.0 - (len(raw_prod_prints) * 1.5)))

    # 9. Serverless Runtime & Cold-Start Hygiene (HTTP timeouts & cached boto3 clients)
    runtime_penalties = (len(missing_http_timeouts) * 15.0) + (len(uncached_boto3_calls) * 5.0)
    runtime_score = max(0.0, min(100.0, 100.0 - runtime_penalties))

    overall_score = (
        (world_model_score * 0.15)
        + (context_score * 0.15)
        + (comprehensibility_score * 0.15)
        + (silent_score * 0.10)
        + (testing_score * 0.15)
        + (iac_score * 0.10)
        + (clone_score * 0.10)
        + (observability_score * 0.05)
        + (runtime_score * 0.05)
    )
    overall_grade = score_to_letter_grade(overall_score)

    top_monoliths = sorted(monoliths_over_1000, reverse=True)[:5]
    monolith_bullets = (
        "\n".join(
            f"  - `{path}` (**{lines:,} lines** · ~{chars // 4:,} tokens per read)"
            for lines, chars, path in top_monoliths
        )
        if top_monoliths
        else "  - None! Zero source files exceed 1,000 lines."
    )

    clone_bullets = (
        "\n".join(
            f"  - **Cluster `{digest}`** ({len(items)} copies · {total_l} lines): "
            + ", ".join(loc for _, loc in items[:4])
            for total_l, digest, items in clone_clusters[:5]
        )
        if clone_clusters
        else "  - None! Zero structural AST clone clusters (>=12 lines) detected."
    )

    report = f"""# 📋 Codebase Report Card: `{repo_name}`

**Overall Agent Readiness Grade:** **{overall_grade}** ({overall_score:.1f} / 100)

| Rubric Dimension | Grade | Score | Key Finding |
| :--- | :---: | :---: | :--- |
| **1. World-Model & Syllabus Legibility** | **{score_to_letter_grade(world_model_score)}** | {world_model_score:.0f}/100 | `AGENTS.md`: {'✅' if has_agents_md else '❌'} · `CODEBASE_MAP.md`: {'✅' if has_codebase_map else '❌'} · `SCHEMA_DICTIONARY.md`: {'✅' if has_schema_dict else '❌'} · ADRs: {adr_count} |
| **2. Context Window Tax (Monolith Bloat)** | **{score_to_letter_grade(context_score)}** | {context_score:.0f}/100 | **{len(monoliths_over_1000)}** / {len(source_files)} files exceed 1,000 lines (~{wasted_tokens_per_turn:,} excess tokens/turn) |
| **3. AST Comprehensibility & Naming** | **{score_to_letter_grade(comprehensibility_score)}** | {comprehensibility_score:.0f}/100 | **{dense_pct:.0f}%** semantically-dense function names · **{type_pct:.0f}%** type-annotated |
| **4. Silent Failure Hygiene** | **{score_to_letter_grade(silent_score)}** | {silent_score:.0f}/100 | **{len(silent_py_excepts)}** silent Python `except: pass` · **{len(silent_js_catches)}** empty JS `catch {{}}` |
| **5. Self-Assessment & Mock Fidelity** | **{score_to_letter_grade(testing_score)}** | {testing_score:.0f}/100 | **{len(test_files)}** test suites · **{total_assertions:,}** assertions · **{unspecced_test_patches}** un-specced `patch()` calls |
| **6. Blast Radius & IaC Isolation** | **{score_to_letter_grade(iac_score)}** | {iac_score:.0f}/100 | **{len(cfn_import_values)}** `Fn::ImportValue` deadlocks · **{len(hardcoded_iam_arns)}** hardcoded IAM ARNs |
| **7. Structural AST Clone Hygiene (DRY)** | **{score_to_letter_grade(clone_score)}** | {clone_score:.0f}/100 | **{len(clone_clusters)}** AST clone clusters ({duplicated_ast_lines} duplicated lines across functions >=12 lines) |
| **8. Observability & PII-Safe Logging** | **{score_to_letter_grade(observability_score)}** | {observability_score:.0f}/100 | **{structured_prod_logs:,}** structured `logger.*` calls · **{len(raw_prod_prints)}** raw `print()` bypasses in prod |
| **9. Serverless Runtime & Cold-Start Hygiene** | **{score_to_letter_grade(runtime_score)}** | {runtime_score:.0f}/100 | **{len(missing_http_timeouts)}** HTTP calls missing `timeout=` · **{len(uncached_boto3_calls)}** uncached `boto3` calls |

---

## 🔍 Top Context-Window Offenders (Files > 1,000 Lines)
{monolith_bullets}

## 🧬 Top Structural AST Clone Clusters (Alpha-Normalized AST Comparison)
{clone_bullets}

---

## 🛠️ Prioritized 30-Day Remediation Roadmap
1. **Install the Classroom Syllabus:** {'Already installed (`AGENTS.md`, `CODEBASE_MAP.md`, `SCHEMA_DICTIONARY.md`).' if (has_agents_md and has_codebase_map and has_schema_dict) else 'Add `AGENTS.md`, `CODEBASE_MAP.md`, and `SCHEMA_DICTIONARY.md` so coding agents stop burning 40,000 tokens grepping for architecture boundaries.'}
2. **Split Monoliths into Declarative Manifests:** {f'Extract static data/prompts from the {len(monoliths_over_1000)} files over 1,000 lines to save ~{wasted_tokens_per_turn:,} tokens per agent read.' if monoliths_over_1000 else 'Keep enforcing the <1,000-line ceiling via `validate_repo_hygiene.py`.'}
3. **Consolidate Structural AST Clones & Silent Fallbacks:** {f'Extract the {len(clone_clusters)} AST clone clusters into shared utilities and replace the {total_silent} silent `except: pass` / `catch {{}}` blocks with descriptive logging.' if (clone_clusters or total_silent) else 'Zero AST clones or silent fallbacks detected—keep enforcing via AST pre-commit checks.'}

> **🍎 Want help turning this Report Card into an A+?**  
> Book a **1-Day PD Day (Socratic Red-Teaming Clinic)** or **The 4-Part Monorepo Makeover** at **[gregorysenyshyn.com/training](https://gregorysenyshyn.com/training)**.
"""
    metrics = {
        "overall_score": round(overall_score, 1),
        "overall_grade": overall_grade,
        "monoliths_over_1000": len(monoliths_over_1000),
        "silent_failures": total_silent,
        "ast_clone_clusters": len(clone_clusters),
        "unspecced_test_patches": unspecced_test_patches,
        "raw_prod_prints": len(raw_prod_prints),
        "missing_http_timeouts": len(missing_http_timeouts),
        "uncached_boto3_calls": len(uncached_boto3_calls),
        "test_files": len(test_files),
        "source_files": len(source_files),
    }
    return report, metrics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an Agent Readiness & Cloud Hygiene Report Card for a repository."
    )
    parser.add_argument(
        "repo_path",
        nargs="?",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Path to the repository root (defaults to current repo).",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Optional file path to write the Markdown Report Card.",
    )
    args = parser.parse_args()

    report_md, _ = audit_repository_and_generate_report_card(args.repo_path)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_md)
        print(f"✅ Wrote Codebase Report Card to {args.output}")
    else:
        print(report_md)


if __name__ == "__main__":
    main()
