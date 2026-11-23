"""Archive-read guard: no unbounded zip or jar payload read anywhere in `spw/`.

SPW reads jar entry names only (`jar_ownership.py` calls `ZipFile.namelist()` and never reads a payload), so there is nothing to
bound today and this test passes with no baseline. It fails on any new `ZipFile.read(...)` or `.open(...).read()` with no size, so a
future payload read must be bounded (read a capped number of bytes, or stream in chunks) before it can land. Adapted from
BridgeForge's `tests/test_safeio_ratchet.py` (`BoundedZipReadTests`). The bound does not protect central-directory parsing.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "spw"

def _is_zipfile(node: ast.AST) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    return (isinstance(target, ast.Attribute) and target.attr == "ZipFile") or (isinstance(target, ast.Name) and target.id == "ZipFile")


def _mentions_zipfile(node: ast.AST | None) -> bool:
    """An expression with a ZipFile in it anywhere: `ZipFile(...)` inside `cache.get(s) or cache.setdefault(s, ZipFile(p))`
    (supersession, missed until the 2026-11-17 review), or an annotation such as `zipfile.ZipFile | None`."""
    return node is not None and any(_is_zipfile(sub) for sub in ast.walk(node) if isinstance(sub, (ast.Call, ast.Attribute, ast.Name)))


def _scopes(tree: ast.Module) -> list[list[ast.AST]]:
    """The nodes of the module's own statements, then of each function (with the functions nested in it, for closures). Names
    are matched per scope: matched module-wide, a plain `with open(q, 'rb') as h: h.read()` was flagged because another
    function used `with a.open(i) as h` (review 2026-11-17)."""
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    top = [node for statement in tree.body if not isinstance(statement, (*functions, ast.ClassDef)) for node in ast.walk(statement)]
    return [top, *[list(ast.walk(node)) for node in ast.walk(tree) if isinstance(node, functions)]]


def _zip_open(node: ast.AST, zips: set[str]) -> bool:
    """`z.open(...)` on a zip name of the scope, or `ZipFile(...).open(...)`."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open"):
        return False
    owner = node.func.value
    return (isinstance(owner, ast.Name) and owner.id in zips) or (isinstance(owner, ast.Call) and _is_zipfile(owner))


def _scope_reads(nodes: list[ast.AST]) -> set[int]:
    zips: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            zips |= {item.optional_vars.id for item in node.items
                     if isinstance(item.optional_vars, ast.Name) and isinstance(item.context_expr, ast.Call) and _is_zipfile(item.context_expr)}
        elif isinstance(node, ast.Assign) and _mentions_zipfile(node.value):
            zips |= {target.id for target in node.targets if isinstance(target, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and (_mentions_zipfile(node.value) or _mentions_zipfile(node.annotation)):
            zips.add(node.target.id)
        elif isinstance(node, ast.arg) and _mentions_zipfile(node.annotation):
            zips.add(node.arg)
    handles: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            handles |= {item.optional_vars.id for item in node.items if isinstance(item.optional_vars, ast.Name) and _zip_open(item.context_expr, zips)}
        elif isinstance(node, ast.Assign) and _zip_open(node.value, zips):
            handles |= {target.id for target in node.targets if isinstance(target, ast.Name)}
    lines: set[int] = set()
    for node in nodes:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "read"):
            continue
        owner, bare = node.func.value, not node.args and not node.keywords
        if (isinstance(owner, ast.Call) and _is_zipfile(owner)) or (isinstance(owner, ast.Name) and owner.id in zips):
            lines.add(node.lineno)
        elif bare and ((isinstance(owner, ast.Name) and owner.id in handles) or _zip_open(owner, zips)):
            lines.add(node.lineno)
    return lines


def unbounded_zip_reads(text: str) -> list[int]:
    """Lines that read a zip entry whole with no size bound: `.read(...)` on a ZipFile, or `.read()` without an argument on a
    handle from its `.open(...)` (named, or chained as `z.open(x).read()`)."""
    return sorted(set().union(*(_scope_reads(nodes) for nodes in _scopes(ast.parse(text)))))


def unbounded_zip_reads_by_module() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        lines = unbounded_zip_reads(path.read_text(encoding="utf-8", errors="replace"))
        if lines:
            found[relative] = lines
    return found


class ArchiveReadGuardTests(unittest.TestCase):
    """A hostile jar entry must not be read into memory whole."""

    def test_no_module_reads_a_zip_entry_without_a_bound(self) -> None:
        self.assertEqual(unbounded_zip_reads_by_module(), {},
                         "read zip and jar entries a bounded number of bytes at a time, never whole")

    def test_the_detector_finds_each_unbounded_form_and_passes_the_bounded_ones(self) -> None:
        cases = {
            "import zipfile\nwith zipfile.ZipFile(p) as a:\n    a.read('x')\n": [3],
            "import zipfile\ndef f(a: zipfile.ZipFile):\n    return a.read('x')\n": [3],
            "from zipfile import ZipFile\njar = ZipFile(p)\ndata = jar.read('x')\n": [3],
            "import zipfile\nzipfile.ZipFile(p).read('x')\n": [2],
            "import zipfile\nwith zipfile.ZipFile(p) as a:\n    with a.open(i) as h:\n        h.read()\n": [4],
            "import zipfile\nwith zipfile.ZipFile(p) as a:\n    with a.open(i) as h:\n        h.read(1024)\n": [],
            "with open(p, 'rb') as h:\n    h.read()\n": [],
            # Forms the 2026-11-17 review found the first detector missed, and the false positive it found.
            "import zipfile\nhandle = cache.get(s) or cache.setdefault(s, zipfile.ZipFile(p))\nhandle.read('x')\n": [3],
            "import zipfile\ndef f(a: zipfile.ZipFile | None):\n    return a.read('x')\n": [3],
            "from zipfile import ZipFile\na: ZipFile = ZipFile(p)\na.read('x')\n": [3],
            "import zipfile\nwith zipfile.ZipFile(p) as a:\n    a.open('x').read()\n": [3],
            "import zipfile\nwith zipfile.ZipFile(p) as a:\n    h = a.open('x')\n    h.read()\n": [4],
            ("import zipfile\ndef f(p):\n    with zipfile.ZipFile(p) as a:\n        with a.open('x') as h:\n            h.read(10)\n"
             "def g(q):\n    with open(q, 'rb') as h:\n        return h.read()\n"): [],
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(unbounded_zip_reads(source), expected)




if __name__ == "__main__":
    unittest.main()
