"""
Dependency / call graph for Python projects (stdlib `ast`, no LLM).

Nodes : module, class, function, method
Edges : import (module -> module) and call (caller -> callee)

Call resolution is static and best-effort. What we CAN resolve exactly:
  - a call to a function/class defined in the same file
  - self.method() inside a class
  - a call through `from x import f` or `import x` when x is in the project
What we can't know without running the code is `obj.method()` on an arbitrary
object. If the method name is unique in the whole project we add an edge
marked `approx: true`; otherwise we leave it out rather than guess.
"""

import ast
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from pathlib import Path

from app.ingestion.file_walker import walk_project

MAX_FILES_FOR_CYCLES = 1500


@dataclass
class Node:
    id: str
    file: str
    name: str
    kind: str            # module | class | function | method
    start: int
    end: int
    decorated: bool = False


@dataclass
class Edge:
    source: str
    target: str
    kind: str            # import | call
    line: int
    approx: bool = False
    count: int = 1


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # ---- queries -----------------------------------------------------------
    def _index(self):
        if not hasattr(self, "_out"):
            self._out, self._in = defaultdict(list), defaultdict(list)
            for e in self.edges:
                if e.kind == "call":
                    self._out[e.source].append(e)
                    self._in[e.target].append(e)

    def callees(self, node_id: str) -> list[Edge]:
        self._index(); return self._out.get(node_id, [])

    def callers(self, node_id: str) -> list[Edge]:
        self._index(); return self._in.get(node_id, [])

    def impact(self, node_id: str, max_depth: int = 3) -> list[dict]:
        """Everything that (transitively) calls this node: the blast radius of changing it."""
        self._index()
        seen, out = {node_id}, []
        q = deque([(node_id, 0)])
        while q:
            cur, d = q.popleft()
            if d == max_depth:
                continue
            for e in self._in.get(cur, []):
                if e.source not in seen:
                    seen.add(e.source)
                    out.append({"node": e.source, "depth": d + 1, "line": e.line, "approx": e.approx})
                    q.append((e.source, d + 1))
        return out

    def node_at_line(self, rel_path: str, line: int) -> Node | None:
        best = None
        for n in self.nodes.values():
            if n.file == rel_path and n.kind in ("function", "method") and n.start <= line <= n.end:
                if best is None or (n.end - n.start) < (best.end - best.start):
                    best = n
        return best

    def find(self, rel_path: str, name: str) -> Node | None:
        return self.nodes.get(f"{rel_path}::{name}")

    def context_for(self, node_id: str, limit: int = 4) -> str:
        """Two short lines for an LLM prompt: who calls this, and what it calls."""
        callers = [e for e in self.callers(node_id) if not e.approx][:limit]
        callees = [e for e in self.callees(node_id) if not e.approx][:limit]
        parts = []
        if callers:
            parts.append("Called by: " + ", ".join(self._short(e.source) for e in callers))
        if callees:
            parts.append("Calls: " + ", ".join(self._short(e.target) for e in callees))
        return "\n".join(parts)

    @staticmethod
    def _short(node_id: str) -> str:
        f, _, name = node_id.partition("::")
        return f"{name or '<module>'} ({f})"

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges],
            "stats": self.stats(),
            "notes": self.notes,
        }

    def stats(self) -> dict:
        self._index()
        kinds = defaultdict(int)
        for n in self.nodes.values():
            kinds[n.kind] += 1
        most_called = sorted(
            ({"node": nid, "callers": len({e.source for e in es})} for nid, es in self._in.items()),
            key=lambda x: -x["callers"])[:10]
        unused = [
            n.id for n in self.nodes.values()
            if n.kind in ("function", "method") and not n.decorated
            and not self._in.get(n.id)
            and not (n.name.split(".")[-1].startswith("__") or n.name.split(".")[-1].startswith("test_")
                     or n.name.split(".")[-1] == "main")
        ]
        return {
            "files": kinds["module"], "functions": kinds["function"], "methods": kinds["method"],
            "classes": kinds["class"],
            "call_edges": sum(1 for e in self.edges if e.kind == "call"),
            "import_edges": sum(1 for e in self.edges if e.kind == "import"),
            "most_called": most_called,
            "possibly_unused": sorted(unused)[:50],
            "import_cycles": self._cycles(),
        }

    def _cycles(self) -> list[list[str]]:
        files = [n.id for n in self.nodes.values() if n.kind == "module"]
        if len(files) > MAX_FILES_FOR_CYCLES:
            return []
        adj = defaultdict(set)
        for e in self.edges:
            if e.kind == "import" and e.source != e.target:
                adj[e.source].add(e.target)
        index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]

        def strong(v):
            index[v] = low[v] = counter[0]; counter[0] += 1
            stack.append(v); on.add(v)
            for w in adj[v]:
                if w not in index:
                    strong(w); low[v] = min(low[v], low[w])
                elif w in on:
                    low[v] = min(low[v], index[w])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop(); on.discard(w); comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    out.append(sorted(comp))

        import sys
        old = sys.getrecursionlimit(); sys.setrecursionlimit(max(old, 5000))
        try:
            for f in files:
                if f not in index:
                    strong(f)
        finally:
            sys.setrecursionlimit(old)
        return out


# ---- building --------------------------------------------------------------

def _module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr); node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def build_graph(project_root: str) -> Graph:
    g = Graph()
    root = Path(project_root).resolve()
    files = [f for f in walk_project(str(root)) if f.language == "python"]

    trees: dict[str, ast.Module] = {}
    for f in files:
        try:
            trees[f.path] = ast.parse(Path(f.absolute_path).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, OSError):
            g.notes.append(f"Skipped {f.path}: could not be parsed.")

    mod_to_file = {_module_name(p): p for p in trees}
    defs: dict[str, dict[str, str]] = {}          # file -> {local name -> node id} (top level)
    methods: dict[str, dict[str, dict[str, str]]] = {}   # file -> class -> {method -> node id}
    method_names: dict[str, list[str]] = defaultdict(list)

    # pass 1: nodes
    for rel, tree in trees.items():
        end = max([getattr(n, "end_lineno", 1) or 1 for n in tree.body] + [1])
        g.nodes[rel] = Node(rel, rel, rel, "module", 1, end)
        defs[rel], methods[rel] = {}, {}
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nid = f"{rel}::{n.name}"
                g.nodes[nid] = Node(nid, rel, n.name, "function", n.lineno, n.end_lineno or n.lineno, bool(n.decorator_list))
                defs[rel][n.name] = nid
            elif isinstance(n, ast.ClassDef):
                cid = f"{rel}::{n.name}"
                g.nodes[cid] = Node(cid, rel, n.name, "class", n.lineno, n.end_lineno or n.lineno, bool(n.decorator_list))
                defs[rel][n.name] = cid
                methods[rel][n.name] = {}
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        mid = f"{rel}::{n.name}.{m.name}"
                        g.nodes[mid] = Node(mid, rel, f"{n.name}.{m.name}", "method", m.lineno,
                                            m.end_lineno or m.lineno, bool(m.decorator_list))
                        methods[rel][n.name][m.name] = mid
                        method_names[m.name].append(mid)

    edges: dict[tuple, Edge] = {}

    def add(src, dst, kind, line, approx=False):
        if not dst or src == dst and kind == "import":
            return
        key = (src, dst, kind)
        if key in edges:
            edges[key].count += 1
            edges[key].approx = edges[key].approx and approx
        else:
            edges[key] = Edge(src, dst, kind, line, approx)

    # pass 2: imports -> alias tables, then calls
    for rel, tree in trees.items():
        pkg = _module_name(rel).split(".")
        if not rel.endswith("__init__.py"):
            pkg = pkg[:-1]
        mod_alias: dict[str, str] = {}            # alias -> dotted module in project
        name_alias: dict[str, tuple[str, str]] = {}   # alias -> (file, name)

        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    full = a.name
                    if a.asname:
                        mod_alias[a.asname] = full
                    else:
                        mod_alias[full.split(".")[0]] = full.split(".")[0]
                    if full in mod_to_file:
                        add(rel, mod_to_file[full], "import", n.lineno)
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    up = pkg[: len(pkg) - (n.level - 1)] if n.level - 1 <= len(pkg) else []
                    base = ".".join([p for p in up + ([n.module] if n.module else []) if p])
                if base in mod_to_file:
                    add(rel, mod_to_file[base], "import", n.lineno)
                for a in n.names:
                    alias = a.asname or a.name
                    sub = f"{base}.{a.name}" if base else a.name
                    if sub in mod_to_file:                       # from pkg import module
                        mod_alias[alias] = sub
                        add(rel, mod_to_file[sub], "import", n.lineno)
                    elif base in mod_to_file:
                        name_alias[alias] = (mod_to_file[base], a.name)

        def resolve_name(name: str, cls: str | None) -> tuple[str | None, bool]:
            if name in defs[rel]:
                return defs[rel][name], False
            if name in name_alias:
                f, n_ = name_alias[name]
                return defs.get(f, {}).get(n_), False
            return None, False

        def resolve_attr(dotted: str, cls: str | None) -> tuple[str | None, bool]:
            parts = dotted.split(".")
            head, rest = parts[0], parts[1:]
            if head == "self" and cls and len(rest) == 1:
                return methods[rel].get(cls, {}).get(rest[0]), False
            if head in defs[rel] and defs[rel][head].endswith(f"::{head}") and len(rest) == 1:
                return methods[rel].get(head, {}).get(rest[0]), False        # ClassName.method()
            if head in mod_alias:
                mod = mod_alias[head]
                chain = rest
                while len(chain) > 1 and f"{mod}.{chain[0]}" in mod_to_file:
                    mod = f"{mod}.{chain[0]}"; chain = chain[1:]
                f = mod_to_file.get(mod)
                if f and len(chain) == 1:
                    return defs.get(f, {}).get(chain[0]), False
                if f and len(chain) == 2:
                    return methods.get(f, {}).get(chain[0], {}).get(chain[1]), False
            if head in name_alias and len(rest) == 1:
                f, n_ = name_alias[head]
                return methods.get(f, {}).get(n_, {}).get(rest[0]), False
            last = parts[-1]
            if len(parts) > 1 and len(method_names.get(last, [])) == 1:
                return method_names[last][0], True                            # unique method name
            return None, False

        class V(ast.NodeVisitor):
            def __init__(self):
                self.cls: str | None = None
                self.scope: list[str] = [rel]

            def visit_ClassDef(self, node):
                prev = self.cls
                if not len(self.scope) > 1:
                    self.cls = node.name
                self.generic_visit(node)
                self.cls = prev

            def _fn(self, node):
                if len(self.scope) == 1:
                    nid = f"{rel}::{self.cls}.{node.name}" if self.cls else f"{rel}::{node.name}"
                    self.scope.append(nid)
                    self.generic_visit(node)
                    self.scope.pop()
                else:
                    self.generic_visit(node)

            visit_FunctionDef = _fn
            visit_AsyncFunctionDef = _fn

            def visit_Call(self, node):
                src = self.scope[-1]
                if isinstance(node.func, ast.Name):
                    dst, approx = resolve_name(node.func.id, self.cls)
                elif isinstance(node.func, ast.Attribute):
                    d = _dotted(node.func)
                    dst, approx = resolve_attr(d, self.cls) if d else (None, False)
                else:
                    dst, approx = None, False
                if dst and dst in g.nodes:
                    if g.nodes[dst].kind == "class":                         # Foo() runs Foo.__init__
                        init = methods[g.nodes[dst].file].get(g.nodes[dst].name, {}).get("__init__")
                        dst = init or dst
                    add(src, dst, "call", node.lineno, approx)
                self.generic_visit(node)

        V().visit(tree)

    g.edges = list(edges.values())
    return g


_CACHE: dict[str, tuple[tuple, Graph]] = {}


def _signature(project_root: str) -> tuple:
    files = [f for f in walk_project(project_root) if f.language == "python"]
    newest = max((Path(f.absolute_path).stat().st_mtime_ns for f in files), default=0)
    return (len(files), newest)


def cached_graph(project_root: str) -> Graph:
    """Rebuild only when a Python file was added, removed or modified."""
    key = str(Path(project_root).resolve())
    sig = _signature(key)
    hit = _CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    g = build_graph(key)
    if len(_CACHE) > 8:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (sig, g)
    return g
