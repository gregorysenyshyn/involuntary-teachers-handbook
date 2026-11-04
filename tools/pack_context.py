#!/usr/bin/env python3
"""
Context Packer for Autonomous Agents & Developers.
Packs a target microservice, shared module, or directory into a single, structured,
token-efficient context bundle for LLM reasoning.

Usage:
    python3 tools/pack_context.py <project_or_path>
    python3 tools/pack_context.py email
    python3 tools/pack_context.py common/shared
    python3 tools/pack_context.py apps/scheduler --output scratch/scheduler_ctx.md
    python3 tools/pack_context.py apps/email --stdout
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
    '.git', '.venv', 'venv', 'node_modules', '__pycache__',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', 'dist',
    'build', '.aws-sam', '.gemini', '.DS_Store', 'coverage'
}

EXCLUDED_EXTS = {
    '.pyc', '.pyo', '.png', '.jpg', '.jpeg', '.gif', '.svg',
    '.ico', '.webp', '.pdf', '.mp3', '.wav', '.mp4', '.zip',
    '.tar', '.gz', '.woff', '.woff2', '.ttf', '.eot', '.lock',
    '.map', '.DS_Store'
}

def estimate_tokens(text_or_chars: Any) -> int:
    """Estimates LLM token count using character heuristic (~3.8 chars per token)."""
    chars = len(text_or_chars) if isinstance(text_or_chars, str) else int(text_or_chars)
    return max(1, int(chars / 3.8))

def resolve_target_dir(target: str) -> str:
    """Resolves target string to an existing directory in the monorepo."""
    if os.path.isabs(target) and os.path.isdir(target):
        return target

    # Check relative to REPO_ROOT
    rel_path = os.path.join(REPO_ROOT, target)
    if os.path.isdir(rel_path):
        return rel_path

    # Check under apps/
    app_path = os.path.join(REPO_ROOT, 'apps', target)
    if os.path.isdir(app_path):
        return app_path

    # Check under common/
    common_path = os.path.join(REPO_ROOT, 'common', target)
    if os.path.isdir(common_path):
        return common_path

    raise FileNotFoundError(f"Could not locate target directory: '{target}'")

def collect_files(target_dir: str, include_layer: bool = False) -> List[str]:
    """Recursively collects valid source files, honoring exclusions."""
    collected = []
    for root, dirs, files in os.walk(target_dir):
        # Prune excluded directories in-place
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith('.')]

        # Skip synced shared layer unless explicitly included
        if not include_layer and os.path.basename(root) == 'shared' and 'src/layer/python' in root.replace('\\', '/'):
            dirs.clear()
            continue

        for f in sorted(files):
            _, ext = os.path.splitext(f)
            if ext.lower() in EXCLUDED_EXTS or f in EXCLUDED_EXTS or f.startswith('.'):
                continue
            collected.append(os.path.join(root, f))
    return sorted(collected)

def generate_ascii_tree(target_dir: str, file_list: List[str]) -> str:
    """Builds a readable ASCII tree structure of collected files."""
    lines = [f"{os.path.basename(target_dir)}/"]
    prefix_dict: Dict[str, Any] = {}

    for f in file_list:
        rel = os.path.relpath(f, target_dir)
        parts = rel.split(os.sep)
        curr = prefix_dict
        for p in parts:
            curr = curr.setdefault(p, {})

    def _render(node: Dict[str, Any], prefix: str = "") -> List[str]:
        res = []
        keys = sorted(node.keys())
        for idx, k in enumerate(keys):
            is_last = (idx == len(keys) - 1)
            connector = "└── " if is_last else "├── "
            child_prefix = "    " if is_last else "│   "
            res.append(f"{prefix}{connector}{k}")
            if node[k]:
                res.extend(_render(node[k], prefix + child_prefix))
        return res

    lines.extend(_render(prefix_dict))
    return "\n".join(lines)

def _format_function_signature(fn_node: ast.FunctionDef | ast.AsyncFunctionDef, skip_self: bool = False) -> str:
    """Format a function/method parameter list and return type annotation from its AST node."""
    params: List[str] = []
    pos_args = [a for a in fn_node.args.args if not (skip_self and a.arg in ("self", "cls"))]
    for arg in pos_args:
        ann = f": {ast.unparse(arg.annotation)}" if arg.annotation is not None else ""
        params.append(f"{arg.arg}{ann}")
    if fn_node.args.vararg is not None:
        v_ann = f": {ast.unparse(fn_node.args.vararg.annotation)}" if fn_node.args.vararg.annotation else ""
        params.append(f"*{fn_node.args.vararg.arg}{v_ann}")
    for kwarg in fn_node.args.kwonlyargs:
        kw_ann = f": {ast.unparse(kwarg.annotation)}" if kwarg.annotation is not None else ""
        params.append(f"{kwarg.arg}{kw_ann}")
    if fn_node.args.kwarg is not None:
        k_ann = f": {ast.unparse(fn_node.args.kwarg.annotation)}" if fn_node.args.kwarg.annotation else ""
        params.append(f"**{fn_node.args.kwarg.arg}{k_ann}")
    ret = f" -> {ast.unparse(fn_node.returns)}" if fn_node.returns is not None else ""
    return f"({', '.join(params)}){ret}"


def extract_python_symbols(content: str) -> List[str]:
    """Extracts top-level class and function signatures (with type annotations) using AST."""
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
                        sig = _format_function_signature(item, skip_self=True)
                        symbols.append(f"  └─ `{node.name}.{item.name}{sig}`")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node)
                doc_snippet = f" - {doc.splitlines()[0]}" if doc else ""
                sig = _format_function_signature(node, skip_self=False)
                prefix = "Async Function" if isinstance(node, ast.AsyncFunctionDef) else "Function"
                symbols.append(f"{prefix} `{node.name}{sig}`{doc_snippet}")
    except Exception:
        pass
    return symbols

def get_syntax_lang(filename: str) -> str:
    """Maps file extension to markdown syntax highlighter."""
    ext = os.path.splitext(filename)[1].lower()
    mapping = {
        '.py': 'python',
        '.js': 'javascript',
        '.ts': 'typescript',
        '.json': 'json',
        '.yaml': 'yaml',
        '.yml': 'yaml',
        '.html': 'html',
        '.css': 'css',
        '.scss': 'scss',
        '.sh': 'bash',
        '.md': 'markdown',
        '.sql': 'sql',
    }
    return mapping.get(ext, '')

def pack_context(target_dir: str, include_layer: bool = False) -> Tuple[str, int, int]:
    """Compiles directory into a formatted context bundle."""
    files = collect_files(target_dir, include_layer=include_layer)
    target_name = os.path.relpath(target_dir, REPO_ROOT)
    tree_str = generate_ascii_tree(target_dir, files)

    sections = []
    symbol_sections = []
    total_chars = 0

    for f in files:
        rel_to_repo = os.path.relpath(f, REPO_ROOT)
        try:
            with open(f, 'r', encoding='utf-8', errors='replace') as fp:
                content = fp.read()
        except Exception as e:
            content = f"# Error reading file: {e}"

        total_chars += len(content)
        file_tokens = estimate_tokens(content)
        lang = get_syntax_lang(f)

        # Extract Python symbols if applicable
        if f.endswith('.py'):
            symbols = extract_python_symbols(content)
            if symbols:
                symbol_sections.append(f"**[{rel_to_repo}](file://{f})**\n" + "\n".join(f"- {s}" for s in symbols))

        sections.append(
            f"### [{rel_to_repo}](file://{f}) (~{file_tokens:,} tokens)\n"
            f"```{lang}\n"
            f"{content}\n"
            f"```\n"
        )

    est_tokens = estimate_tokens(total_chars)

    # Assemble Document
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
        doc.extend([
            "## Symbol Outline",
            "",
            "\n\n".join(symbol_sections),
            "",
            "---",
            ""
        ])

    doc.extend([
        "## Source Files",
        "",
        "\n".join(sections)
    ])

    bundle_str = "\n".join(doc)
    return bundle_str, len(files), est_tokens

def main() -> None:
    parser = argparse.ArgumentParser(description="Pack a project or directory into an LLM context bundle.")
    parser.add_argument("target", help="Project name (e.g. 'email') or relative directory path")
    parser.add_argument("--output", "-o", help="Custom output path. Defaults to scratch/context_<target>.md")
    parser.add_argument("--stdout", action="store_true", help="Print bundle directly to stdout")
    parser.add_argument("--clip", action="store_true", help="Copy bundle to clipboard (macOS)")
    parser.add_argument("--include-layer", action="store_true", help="Include synced src/layer/python/shared directory")
    args = parser.parse_args()

    try:
        target_dir = resolve_target_dir(args.target)
    except FileNotFoundError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    clean_name = os.path.basename(os.path.normpath(target_dir))
    bundle_str, file_count, est_tokens = pack_context(target_dir, include_layer=args.include_layer)

    if args.stdout:
        print(bundle_str)
        return

    # Default output path
    scratch_dir = os.path.join(REPO_ROOT, 'scratch')
    os.makedirs(scratch_dir, exist_ok=True)
    out_file = args.output or os.path.join(scratch_dir, f"context_{clean_name}.md")

    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(bundle_str)

    print(f"\n📦 Context Pack Complete!")
    print(f"  Target:     {target_dir}")
    print(f"  Files:      {file_count}")
    print(f"  Est Tokens: ~{est_tokens:,}")
    print(f"  Bundle:     {out_file}")

    if args.clip:
        if sys.platform == 'darwin':
            try:
                proc = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
                proc.communicate(bundle_str.encode('utf-8'))
                print("  📋 Copied to clipboard via pbcopy.")
            except Exception as e:
                print(f"  ⚠️ Failed to copy to clipboard: {e}")
        else:
            print("  ⚠️ Clipboard copy only supported natively on macOS.")

if __name__ == '__main__':
    main()
