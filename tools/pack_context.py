#!/usr/bin/env python3
"""
Zero-Dependency AST Context Packer for Autonomous Coding Agents.
Packs a target microservice, shared module, or directory into a single, structured,
token-efficient Markdown bundle with an ASCII directory tree and Python AST symbol outline.

Usage:
    python3 tools/pack_context.py <directory_or_service_name>
    python3 tools/pack_context.py services/billing --stdout
    python3 tools/pack_context.py services/auth --output scratch/auth_ctx.md
"""

import os
import sys
import ast
import argparse
import subprocess
from datetime import datetime
from typing import List, Tuple, Dict, Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist",
    "build", ".aws-sam", ".gemini", ".DS_Store", "coverage"
}

EXCLUDED_EXTS = {
    ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".webp", ".pdf", ".mp3", ".wav", ".mp4", ".zip",
    ".tar", ".gz", ".woff", ".woff2", ".ttf", ".eot", ".lock",
    ".map", ".DS_Store"
}


def estimate_tokens(text_or_chars: Any) -> int:
    """Estimates LLM token count using character heuristic (~3.8 chars per token)."""
    chars = len(text_or_chars) if isinstance(text_or_chars, str) else int(text_or_chars)
    return max(1, int(chars / 3.8))


def resolve_target_dir(target: str) -> str:
    """Resolves target string to an existing directory in the repository."""
    if os.path.isdir(target):
        return os.path.abspath(target)

    for candidate in [
        os.path.join(REPO_ROOT, target),
        os.path.join(REPO_ROOT, "apps", target),
        os.path.join(REPO_ROOT, "services", target),
        os.path.join(REPO_ROOT, "packages", target),
        os.path.join(REPO_ROOT, "common", target),
    ]:
        if os.path.isdir(candidate):
            return candidate

    raise FileNotFoundError(f"Could not locate target directory: '{target}'")


def collect_files(target_dir: str) -> List[str]:
    """Recursively collects valid source files, honoring exclusions."""
    collected = []
    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]
        for f in sorted(files):
            _, ext = os.path.splitext(f)
            if ext.lower() in EXCLUDED_EXTS or f in EXCLUDED_EXTS or f.startswith("."):
                continue
            collected.append(os.path.join(root, f))
    return sorted(collected)


def generate_ascii_tree(target_dir: str, file_list: List[str]) -> str:
    """Builds a readable ASCII tree structure of collected files."""
    lines = [f"{os.path.basename(target_dir)}/"]
    prefix_dict: Dict[str, Any] = {}

    for f in file_list:
        rel = os.path.relpath(f, target_dir)
        curr = prefix_dict
        for part in rel.split(os.sep):
            curr = curr.setdefault(part, {})

    def _render(node: Dict[str, Any], prefix: str = "") -> List[str]:
        res = []
        keys = sorted(node.keys())
        for idx, k in enumerate(keys):
            is_last = idx == len(keys) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = "    " if is_last else "│   "
            res.append(f"{prefix}{connector}{k}")
            if node[k]:
                res.extend(_render(node[k], prefix + child_prefix))
        return res

    lines.extend(_render(prefix_dict))
    return "\n".join(lines)


def extract_python_symbols(content: str) -> List[str]:
    """Extracts top-level class and function signatures using Python's stdlib AST."""
    symbols = []
    try:
        tree = ast.parse(content)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node)
                doc_snippet = f" - {doc.splitlines()[0]}" if doc else ""
                symbols.append(f"Class `{node.name}`{doc_snippet}")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in item.args.args if a.arg != "self"]
                        symbols.append(f"  └─ `{node.name}.{item.name}({', '.join(args)})`")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                doc_snippet = f" - {doc.splitlines()[0]}" if doc else ""
                args = [a.arg for a in node.args.args]
                symbols.append(f"Function `{node.name}({', '.join(args)})`{doc_snippet}")
    except Exception:
        pass
    return symbols


def get_syntax_lang(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".html": "html", ".css": "css", ".scss": "scss",
        ".sh": "bash", ".md": "markdown", ".sql": "sql",
    }.get(ext, "")


def pack_context(target_dir: str) -> Tuple[str, int, int]:
    files = collect_files(target_dir)
    target_name = os.path.relpath(target_dir, REPO_ROOT)
    tree_str = generate_ascii_tree(target_dir, files)

    sections = []
    symbol_sections = []
    total_chars = 0

    for f in files:
        rel_to_repo = os.path.relpath(f, REPO_ROOT)
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fp:
                content = fp.read()
        except Exception as e:
            content = f"# Error reading file: {e}"

        total_chars += len(content)
        file_tokens = estimate_tokens(content)
        lang = get_syntax_lang(f)

        if f.endswith(".py"):
            symbols = extract_python_symbols(content)
            if symbols:
                symbol_sections.append(
                    f"**[{rel_to_repo}](file://{f})**\n" + "\n".join(f"- {s}" for s in symbols)
                )

        sections.append(
            f"### [{rel_to_repo}](file://{f}) (~{file_tokens:,} tokens)\n"
            f"```{lang}\n{content}\n```\n"
        )

    est_tokens = estimate_tokens(total_chars)
    doc = [
        f"# Context Pack: `{target_name}`",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Files Included:** {len(files)}  ",
        f"**Estimated Tokens:** ~{est_tokens:,}  ",
        "",
        "---",
        "",
        "## Directory Structure",
        "```text",
        tree_str,
        "```",
        "",
    ]

    if symbol_sections:
        doc.extend(["## Symbol Outline", "", "\n\n".join(symbol_sections), "", "---", ""])

    doc.extend(["## Source Files", "", "\n".join(sections)])
    return "\n".join(doc), len(files), est_tokens


def main():
    parser = argparse.ArgumentParser(description="Pack a directory into an LLM context bundle.")
    parser.add_argument("target", help="Service name or relative directory path")
    parser.add_argument("--output", "-o", help="Custom output file path")
    parser.add_argument("--stdout", action="store_true", help="Print bundle directly to stdout")
    parser.add_argument("--clip", action="store_true", help="Copy bundle to clipboard (macOS)")
    args = parser.parse_args()

    try:
        target_dir = resolve_target_dir(args.target)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    clean_name = os.path.basename(os.path.normpath(target_dir))
    bundle_str, file_count, est_tokens = pack_context(target_dir)

    if args.stdout:
        print(bundle_str)
        return

    scratch_dir = os.path.join(REPO_ROOT, "scratch")
    os.makedirs(scratch_dir, exist_ok=True)
    out_file = args.output or os.path.join(scratch_dir, f"context_{clean_name}.md")

    with open(out_file, "w", encoding="utf-8") as fp:
        fp.write(bundle_str)

    print(f"Packed {file_count} files (~{est_tokens:,} tokens) -> {out_file}")

    if args.clip:
        subprocess.run(["pbcopy"], input=bundle_str.encode("utf-8"), check=True)
        print("Copied to macOS clipboard.")


if __name__ == "__main__":
    main()
