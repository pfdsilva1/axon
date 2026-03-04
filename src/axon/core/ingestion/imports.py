"""Phase 4: Import resolution for Axon.

Takes the FileParseData produced by the parsing phase and resolves import
statements to actual File nodes in the knowledge graph, creating IMPORTS
relationships between the importing file and the target file.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from axon.core.graph.graph import KnowledgeGraph
from axon.core.graph.model import (
    GraphRelationship,
    NodeLabel,
    RelType,
    generate_id,
)
from axon.core.ingestion.parser_phase import FileParseData
from axon.core.parsers.base import ImportInfo

logger = logging.getLogger(__name__)

_JS_TS_EXTENSIONS = (".ts", ".js", ".tsx", ".jsx")
_GO_STDLIB_DOMAINS = frozenset({"golang.org", "google.golang.org", "gopkg.in"})
_DART_EXTENSIONS = (".dart",)

def build_file_index(graph: KnowledgeGraph) -> dict[str, str]:
    """Build an index mapping file paths to their graph node IDs.

    Iterates over all :pyclass:`NodeLabel.FILE` nodes in the graph and
    returns a dict keyed by ``file_path`` with node ``id`` as value.

    Args:
        graph: The knowledge graph containing File nodes.

    Returns:
        A dict like ``{"src/auth/validate.py": "file:src/auth/validate.py:"}``.
    """
    file_nodes = graph.get_nodes_by_label(NodeLabel.FILE)
    return {node.file_path: node.id for node in file_nodes}

def resolve_import_path(
    importing_file: str,
    import_info: ImportInfo,
    file_index: dict[str, str],
) -> str | None:
    """Resolve an import statement to the target file's node ID.

    Uses the importing file's path, the parsed :class:`ImportInfo`, and the
    index of all known project files to determine which file is being
    imported.  Returns ``None`` for external/unresolvable imports.

    Args:
        importing_file: Relative path of the file containing the import
            (e.g. ``"src/auth/validate.py"``).
        import_info: The parsed import information.
        file_index: Mapping of relative file paths to their graph node IDs.

    Returns:
        The node ID of the resolved target file, or ``None`` if the import
        cannot be resolved to a file in the project.
    """
    language = _detect_language(importing_file)

    if language == "python":
        return _resolve_python(importing_file, import_info, file_index)
    if language in ("typescript", "javascript"):
        return _resolve_js_ts(importing_file, import_info, file_index)
    if language == "go":
        return _resolve_go(importing_file, import_info, file_index)
    if language == "dart":
        return _resolve_dart(importing_file, import_info, file_index)

    return None

def process_imports(
    parse_data: list[FileParseData],
    graph: KnowledgeGraph,
) -> None:
    """Resolve imports and create IMPORTS relationships in the graph.

    For each file's parsed imports, resolves the target file and creates
    an ``IMPORTS`` relationship from the importing file node to the target
    file node.  Duplicate edges (same source -> same target) are skipped.

    Args:
        parse_data: Parse results from the parsing phase.
        graph: The knowledge graph to populate with IMPORTS relationships.
    """
    file_index = build_file_index(graph)
    seen: set[tuple[str, str]] = set()

    for fpd in parse_data:
        source_file_id = generate_id(NodeLabel.FILE, fpd.file_path)

        for imp in fpd.parse_result.imports:
            target_id = resolve_import_path(fpd.file_path, imp, file_index)
            if target_id is None:
                continue

            pair = (source_file_id, target_id)
            if pair in seen:
                continue
            seen.add(pair)

            rel_id = f"imports:{source_file_id}->{target_id}"
            graph.add_relationship(
                GraphRelationship(
                    id=rel_id,
                    type=RelType.IMPORTS,
                    source=source_file_id,
                    target=target_id,
                    properties={"symbols": ",".join(imp.names)},
                )
            )

def _detect_language(file_path: str) -> str:
    """Infer language from a file's extension."""
    suffix = PurePosixPath(file_path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in (".ts", ".tsx"):
        return "typescript"
    if suffix in (".js", ".jsx"):
        return "javascript"
    if suffix == ".go":
        return "go"
    if suffix == ".dart":
        return "dart"
    return ""

def _resolve_python(
    importing_file: str,
    import_info: ImportInfo,
    file_index: dict[str, str],
) -> str | None:
    """Resolve a Python import to a file node ID.

    Handles:
    - Relative imports (``is_relative=True``): dot-prefixed module paths
      resolved relative to the importing file's directory.
    - Absolute imports: treated as dotted paths from the project root.

    Returns ``None`` for external (not in file_index) imports.
    """
    if import_info.is_relative:
        return _resolve_python_relative(importing_file, import_info, file_index)
    return _resolve_python_absolute(import_info, file_index)

def _resolve_python_relative(
    importing_file: str,
    import_info: ImportInfo,
    file_index: dict[str, str],
) -> str | None:
    """Resolve a relative Python import (``from .foo import bar``).

    The number of leading dots determines how many directory levels to
    traverse upward from the importing file's parent directory.

    ``from .utils import helper``  -> one dot  -> same directory
    ``from ..models import User``  -> two dots -> parent directory
    """
    module = import_info.module

    dot_count = 0
    for ch in module:
        if ch == ".":
            dot_count += 1
        else:
            break

    remainder = module[dot_count:]

    base = PurePosixPath(importing_file).parent
    for _ in range(dot_count - 1):
        base = base.parent

    if remainder:
        segments = remainder.split(".")
        target_dir = base / PurePosixPath(*segments)
    else:
        target_dir = base

    return _try_python_paths(str(target_dir), file_index)

def _resolve_python_absolute(
    import_info: ImportInfo,
    file_index: dict[str, str],
) -> str | None:
    """Resolve an absolute Python import (``from mypackage.auth import validate``).

    Converts the dotted module path to a filesystem path and looks it up
    in the file index.  Returns ``None`` for external packages not present
    in the project.
    """
    module = import_info.module
    segments = module.split(".")
    target_path = str(PurePosixPath(*segments))
    return _try_python_paths(target_path, file_index)

def _try_python_paths(base_path: str, file_index: dict[str, str]) -> str | None:
    """Try common Python file resolution patterns for *base_path*.

    Checks in order:
    1. ``base_path.py`` (direct module file)
    2. ``base_path/__init__.py`` (package directory)
    """
    candidates = [
        f"{base_path}.py",
        f"{base_path}/__init__.py",
    ]
    for candidate in candidates:
        if candidate in file_index:
            return file_index[candidate]
    return None

def _resolve_js_ts(
    importing_file: str,
    import_info: ImportInfo,
    file_index: dict[str, str],
) -> str | None:
    """Resolve a JavaScript/TypeScript import to a file node ID.

    Relative imports (starting with ``./`` or ``../``) are resolved against
    the importing file's directory.  Bare specifiers (e.g. ``'express'``)
    are treated as external and return ``None``.
    """
    module = import_info.module

    if not module.startswith("."):
        return None

    base = PurePosixPath(importing_file).parent
    resolved = base / module

    resolved_str = str(PurePosixPath(*resolved.parts))

    return _try_js_ts_paths(resolved_str, file_index)

def _resolve_go(
    importing_file: str,
    import_info: ImportInfo,
    file_index: dict[str, str],
) -> str | None:
    """Resolve a Go import path to a file node ID.

    Go uses absolute module paths only (no relative imports).  Standard
    library imports (single-word or ``pkg/subpkg`` without a domain) and
    well-known third-party domains are treated as external and skipped.
    For project-internal paths the last segment of the import path is used
    to locate a matching directory in the file index.

    Examples::

        "fmt"                    -> external (stdlib single word)
        "net/http"               -> external (stdlib multi-segment, no domain)
        "github.com/user/pkg"    -> external (third-party domain)
        "myapp/models"           -> look for files under models/
    """
    module = import_info.module

    # External: starts with a known third-party domain.
    first_segment = module.split("/")[0]
    if "." in first_segment:
        return None

    # External: stdlib (no domain, single word or stdlib paths like "net/http").
    # Heuristic: if none of the path segments look like an internal package
    # (i.e. no segment matches any top-level directory in the file index),
    # treat as stdlib/external.
    last_segment = module.rsplit("/", 1)[-1]

    # Try to find files inside a directory named after the last path segment.
    for file_path in file_index:
        parts = PurePosixPath(file_path).parts
        # Check if the last segment of the import matches any directory component.
        if last_segment in parts[:-1]:  # directory match (not the file name itself)
            return file_index[file_path]

    return None


def _resolve_dart(
    importing_file: str,
    import_info: ImportInfo,
    file_index: dict[str, str],
) -> str | None:
    """Resolve a Dart import to a file node ID.

    Handles three import forms:

    * ``dart:async`` — Dart core library, always external.
    * ``package:flutter/material.dart`` — external Flutter/pub package.
    * ``package:myapp/...`` — same-project package import; resolved by
      stripping the ``package:<name>/`` prefix and looking up the remainder
      as a relative path from the project root.
    * Relative imports (``./helper.dart``, ``../utils.dart``) — resolved
      against the importing file's directory, identical to JS/TS resolution.
    """
    module = import_info.module

    # Dart core library — always external.
    if module.startswith("dart:"):
        return None

    # Relative import — resolve like JS/TS.
    if module.startswith("."):
        base = PurePosixPath(importing_file).parent
        resolved = str(base / module)
        if resolved in file_index:
            return file_index[resolved]
        return None

    # package: import
    if module.startswith("package:"):
        remainder = module[len("package:"):]
        # Drop the package name (first segment) to get the path within lib/.
        # e.g. "package:myapp/models/user.dart" -> "models/user.dart"
        slash_idx = remainder.find("/")
        if slash_idx == -1:
            return None
        inner_path = remainder[slash_idx + 1:]

        # Direct lookup.
        if inner_path in file_index:
            return file_index[inner_path]

        # Try under lib/ (common Dart project layout).
        lib_path = f"lib/{inner_path}"
        if lib_path in file_index:
            return file_index[lib_path]

        return None

    return None


def _try_js_ts_paths(base_path: str, file_index: dict[str, str]) -> str | None:
    """Try common JS/TS file resolution patterns for *base_path*.

    Checks in order:
    1. ``base_path`` as-is (already has extension)
    2. ``base_path`` + each known extension (.ts, .js, .tsx, .jsx)
    3. ``base_path/index`` + each known extension
    """
    # 1. Exact match (import already includes extension).
    if base_path in file_index:
        return file_index[base_path]

    # 2. Try appending extensions.
    for ext in _JS_TS_EXTENSIONS:
        candidate = f"{base_path}{ext}"
        if candidate in file_index:
            return file_index[candidate]

    # 3. Try as directory with index file.
    for ext in _JS_TS_EXTENSIONS:
        candidate = f"{base_path}/index{ext}"
        if candidate in file_index:
            return file_index[candidate]

    return None
