"""Read-only, static Python framework inventory for reviewable module proposals.

This scanner never imports or runs the inspected source. Semantic decomposition
of arbitrary tasks remains with the Skill host; import edges are proposals for
the caller to review, not inferred execution authorization.
"""

import ast
import hashlib
import tokenize
from pathlib import Path

from .algorithms import Task, topological_order
from .core import GranuleError, _text


IGNORED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".agentgranule", "build", "dist", "tests"}


def _bounded_path(path: Path, allowed_root: Path) -> Path:
    """Check resolved paths before traversing or reading bounded source."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise GranuleError("Cannot resolve source path within allowed_root") from exc
    if not resolved.is_relative_to(allowed_root):
        raise GranuleError("Source path must stay within allowed_root")
    return resolved


def _bounded_python_files(source: Path, allowed_root: Path) -> list[Path]:
    """Validate directory links, including Windows junctions, before descent."""
    files = []
    pending = [(source, ())]
    while pending:
        directory, ancestors = pending.pop()
        resolved = _bounded_path(directory, allowed_root)
        if not resolved.is_relative_to(source):
            raise GranuleError("Source directories must stay within the source directory")
        if resolved in ancestors:
            raise GranuleError("Cyclic source directory links are not supported")
        try:
            children = sorted(resolved.iterdir())
        except OSError as exc:
            raise GranuleError("Cannot list source directory within allowed_root") from exc
        for child in children:
            if child.name in IGNORED or child.name.endswith(".egg-info"):
                continue
            target = _bounded_path(child, allowed_root)
            if not target.is_relative_to(source):
                raise GranuleError("Source paths must stay within the source directory")
            # Preserve the logical path used for module IDs, even for internal links.
            logical = directory / child.name
            if target.is_dir():
                pending.append((logical, (*ancestors, resolved)))
            elif child.match("*.py") and target.is_file():
                files.append(logical)
    return sorted(files)


def analyze_framework(root_path: str, *, allowed_root: str | Path | None = None) -> dict:
    """Inventory .py modules, public symbols and local imports into an analysis.

    Prefer root/src when present. Files outside the source root (including
    symlinks) are rejected. Cyclic imports are returned as separate relationships,
    with an empty proposed execution DAG for human revision, never silently lost.
    An optional allowed_root also bounds traversal before entering directories.
    Path checks assume no concurrent link replacement; this is not an OS sandbox.
    """
    _text(root_path, "root_path")
    boundary = None
    if allowed_root is not None:
        _text(str(allowed_root), "allowed_root")
        boundary = Path(allowed_root).resolve()
        if not boundary.is_dir():
            raise GranuleError("allowed_root must be an existing directory")
    root = _bounded_path(Path(root_path), boundary) if boundary is not None else Path(root_path).resolve()
    if not root.is_dir():
        raise GranuleError("root_path must be an existing directory")
    source_candidate = _bounded_path(root / "src", boundary) if boundary is not None else root / "src"
    source = source_candidate.resolve() if source_candidate.is_dir() else root
    if not source.is_relative_to(root):
        raise GranuleError("Source directory must stay within root_path")
    files = (_bounded_python_files(source, boundary) if boundary is not None else
             [file for file in sorted(source.rglob("*.py"))
              if not any(part in IGNORED or part.endswith(".egg-info") for part in file.relative_to(source).parts)])
    if not files:
        raise GranuleError("No Python framework modules found; the host must analyze other materials")
    entries = {}
    file_hashes = {}
    root_package = (source / "__init__.py" in files) if boundary is not None else (source / "__init__.py").is_file()
    for file in files:
        read_path = _bounded_path(file, boundary) if boundary is not None else file.resolve()
        if not read_path.is_relative_to(source):
            raise GranuleError("Source files must stay within the source directory")
        relative = file.relative_to(source)
        parts = list(relative.with_suffix("").parts)
        if root_package:
            parts.insert(0, source.name)
        is_package = parts[-1] == "__init__"
        name = ".".join(parts[:-1] if is_package else parts)
        if not name:
            name = source.name
        if name in entries:
            raise GranuleError(f"Ambiguous Python module name {name}; review the source layout")
        try:
            with tokenize.open(read_path if boundary is not None else file) as stream:
                source_text = stream.read()
            tree = ast.parse(source_text, filename=str(relative))
            file_hashes[relative.as_posix()] = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise GranuleError(f"Cannot analyze {relative.as_posix()}: {exc}") from exc
        symbols = [node.name for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                   and not node.name.startswith("_")]
        # Read a module description rather than inventing task semantics from names.
        description = (ast.get_docstring(tree) or f"Python 模块 {name}").split("\n\n", 1)[0].strip()
        entries[name] = {"id": relative.as_posix(), "name": name, "description": description,
                         "expected_output": f"{name} 的设计说明与接口约束", "directions": ["design"],
                         "parent_id": None, "depends_on": [],
                         "basis": f"源码：{relative.as_posix()}；公开定义：{', '.join(symbols) or '包导出／入口'}",
                         "tree": tree, "package": name.split(".") if is_package else name.split(".")[:-1]}
    relationships = set()
    for name, entry in entries.items():
        for node in ast.walk(entry["tree"]):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    package = entry["package"]
                    if node.level > len(package):
                        continue
                    prefix = package[:len(package) - node.level + 1]
                    base = ".".join([*prefix, *node.module.split(".")] if node.module else prefix)
                else:
                    base = node.module or ""
                targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            for target in targets:
                if target in entries and target != name:
                    relationships.add((entry["id"], entries[target]["id"]))
    by_id = {entry["id"]: entry for entry in entries.values()}
    for consumer, dependency in sorted(relationships):
        by_id[consumer]["depends_on"].append(dependency)
    warnings = []
    try:
        topological_order(Task(entry["id"], entry["id"], "design", entry["depends_on"]) for entry in entries.values())
    except GranuleError:
        warnings.append("源码导入关系有循环；完整关系保存在 import_relationships，执行依赖需人工设置，尚未批准。")
        for entry in entries.values():
            entry["depends_on"] = []
    modules = [{key: value for key, value in entry.items() if key not in {"tree", "package"}}
               for entry in entries.values()]
    return {"analysis": {"summary": "依据实际 Python 源码形成的框架模块清单，导入关系仅为待审阅的依赖建议。",
                         "modules": modules,
                         "context": {"source_root": str(source), "file_hashes": file_hashes, "import_relationships": [
                             {"module_id": consumer, "depends_on": dependency} for consumer, dependency in sorted(relationships)],
                                     "warnings": warnings}},
            "source_root": str(source), "module_count": len(modules), "warnings": warnings}
