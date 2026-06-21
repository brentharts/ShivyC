"""rpy_pgo.py -- profile-guided auto-typing for the py2c rpython front end.

Static inference can only see literal/arithmetic shapes; a profiling pass sees
the *actual* runtime types. This module implements a `-fprofile-generate`-style
flow (the name mirrors gcc, but we inject type probes, not timers):

    1. instrument()  -- parse the user's script and inject, per assignment /
       container mutation, a `__rpy_rec(key, value)` probe that captures the
       runtime element/key/value types into a module-global dict. Every `for` /
       `while` loop is also bounded to a small iteration budget so a long script
       profiles quickly (we only need a few iterations to see the types).
    2. profile()     -- run the instrumented script in a subprocess; an atexit
       hook dumps the captured types to a JSON file in /tmp.
    3. generate_autotyped() -- read the observed types back and rewrite the
       *original* source, annotating each cleanly-typed, non-escaping empty
       list/dict as `name: "list[int]"` / `name: "dict[str,int]"`. py2c's
       existing typed-container path then lowers them to the unboxed form.

`autotype()` orchestrates all three and is what py2c calls. It is best-effort:
on *any* failure (script error, timeout, no observations) it returns the
original path unchanged, so enabling the flag can never break a build.
"""

import ast
import os
import subprocess
import sys
import tempfile

SCALARS = {"int", "float", "bool", "str"}
_DEFAULT_BUDGET = 8

# Probe runtime prepended to the instrumented script. Captures, per
# `scope::name` key, the set of scalar element/key/value types observed.
_PREAMBLE = r'''
import json as __rpy_json, atexit as __rpy_atexit, os as __rpy_os
__RPY = {}
def __rpy_scal(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if v is None:
        return "None"
    return "obj"
def __rpy_add(lst, t):
    if t not in lst:
        lst.append(t)
def __rpy_rec(key, val):
    e = __RPY.get(key)
    if e is None:
        e = __RPY[key] = {"kind": "", "elem": [], "key": [], "val": []}
    try:
        if isinstance(val, list):
            e["kind"] = "list"
            for x in val[:64]:
                __rpy_add(e["elem"], __rpy_scal(x))
        elif isinstance(val, dict):
            e["kind"] = "dict"
            for k in list(val)[:64]:
                __rpy_add(e["key"], __rpy_scal(k))
            for v in list(val.values())[:64]:
                __rpy_add(e["val"], __rpy_scal(v))
        elif isinstance(val, set):
            e["kind"] = "set"
            for x in list(val)[:64]:
                __rpy_add(e["elem"], __rpy_scal(x))
        else:
            e["kind"] = "scalar"
            __rpy_add(e["val"], __rpy_scal(val))
    except Exception:
        pass
def __rpy_dump():
    try:
        p = __rpy_os.environ.get("RPY_TYPES_OUT", "/tmp/rpy_types.json")
        with open(p, "w") as f:
            __rpy_json.dump(__RPY, f)
    except Exception:
        pass
__rpy_atexit.register(__rpy_dump)
'''

_MUTATORS = {"append", "add", "extend", "update", "insert",
             "setdefault", "__setitem__"}


# --------------------------------------------------------------------------
# 1. instrumentation
# --------------------------------------------------------------------------
class _Instrumenter:
    """Inject type probes and bound every loop. Operates on statement lists so
    it can insert probe statements as siblings after the statement they probe."""

    def __init__(self, budget):
        self.budget = budget
        self.loop_id = 0

    def run(self, tree):
        tree.body = self._body(tree.body, "module")
        ast.fix_missing_locations(tree)
        return tree

    def _body(self, stmts, scope):
        out = []
        for s in stmts:
            if isinstance(s, (ast.For, ast.While)):
                out.extend(self._bound_loop(s, scope))
                continue
            self._recurse(s, scope)
            out.append(s)
            out.extend(self._probes(s, scope))
        return out

    def _recurse(self, s, scope):
        """Instrument the bodies of compound statements in place."""
        if isinstance(s, ast.FunctionDef):
            s.body = self._body(s.body, s.name)
        elif isinstance(s, ast.AsyncFunctionDef):
            s.body = self._body(s.body, s.name)
        elif isinstance(s, ast.ClassDef):
            s.body = self._body(s.body, scope)
        elif isinstance(s, ast.If):
            s.body = self._body(s.body, scope)
            s.orelse = self._body(s.orelse, scope)
        elif isinstance(s, ast.With):
            s.body = self._body(s.body, scope)
        elif isinstance(s, ast.Try):
            s.body = self._body(s.body, scope)
            for h in s.handlers:
                h.body = self._body(h.body, scope)
            s.orelse = self._body(s.orelse, scope)
            s.finalbody = self._body(s.finalbody, scope)

    def _bound_loop(self, loop, scope):
        cname = "__rpy_lc_%d" % self.loop_id
        self.loop_id += 1
        init = ast.Assign(targets=[ast.Name(id=cname, ctx=ast.Store())],
                          value=ast.Constant(value=0))
        incr = ast.AugAssign(target=ast.Name(id=cname, ctx=ast.Store()),
                             op=ast.Add(), value=ast.Constant(value=1))
        guard = ast.If(
            test=ast.Compare(left=ast.Name(id=cname, ctx=ast.Load()),
                             ops=[ast.Gt()],
                             comparators=[ast.Constant(value=self.budget)]),
            body=[ast.Break()], orelse=[])
        inner = self._body(loop.body, scope)
        loop.body = [incr, guard] + inner
        if isinstance(loop, ast.For):
            loop.orelse = self._body(loop.orelse, scope)
        else:
            loop.orelse = self._body(loop.orelse, scope)
        return [init, loop]

    def _probe_names(self, s):
        """Container/variable names this statement defines or mutates."""
        names = []
        if isinstance(s, ast.Assign):
            for t in s.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
                elif isinstance(t, (ast.Tuple, ast.List)):
                    names += [el.id for el in t.elts if isinstance(el, ast.Name)]
                elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    names.append(t.value.id)
        elif isinstance(s, ast.AugAssign):
            t = s.target
            if isinstance(t, ast.Name):
                names.append(t.id)
            elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                names.append(t.value.id)
        elif isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) \
                and s.value is not None:
            names.append(s.target.id)
        elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
            f = s.value.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.attr in _MUTATORS:
                names.append(f.value.id)
        seen, uniq = set(), []
        for n in names:
            if n not in seen and not n.startswith("__rpy_"):
                seen.add(n)
                uniq.append(n)
        return uniq

    def _probes(self, s, scope):
        out = []
        for nm in self._probe_names(s):
            call = ast.Expr(ast.Call(
                func=ast.Name(id="__rpy_rec", ctx=ast.Load()),
                args=[ast.Constant(value="%s::%s" % (scope, nm)),
                      ast.Name(id=nm, ctx=ast.Load())],
                keywords=[]))
            out.append(call)
        return out


def instrument(src, budget=_DEFAULT_BUDGET):
    tree = ast.parse(src)
    tree = _Instrumenter(budget).run(tree)
    return _PREAMBLE + "\n" + ast.unparse(tree)


# --------------------------------------------------------------------------
# 2. profiling run
# --------------------------------------------------------------------------
def profile(src, basename, timeout=20):
    """Run the instrumented source in a subprocess; return the observed type
    map (dict) or {} on any failure."""
    import json
    work = tempfile.mkdtemp(prefix="rpy_pgo_")
    inst_path = os.path.join(work, "instr_" + basename)
    types_out = os.path.join(work, "types.json")
    try:
        with open(inst_path, "w", encoding="utf-8") as f:
            f.write(src)
        env = dict(os.environ)
        env["RPY_TYPES_OUT"] = types_out
        # Strip the profiling flag so a nested py2c call can't recurse.
        env.pop("RPY_PROFILE_GENERATE", None)
        subprocess.run([sys.executable, inst_path],
                       env=env, timeout=timeout,
                       stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=False)
        if os.path.exists(types_out):
            with open(types_out, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# --------------------------------------------------------------------------
# 3. rewrite the original source with observed annotations
# --------------------------------------------------------------------------
def _empty_container_kind(node):
    if isinstance(node, ast.Dict) and not node.keys:
        return "dict"
    if isinstance(node, ast.List) and not node.elts:
        return "list"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "dict" and not node.args:
            return "dict"
        if node.func.id == "list" and not node.args:
            return "list"
    return None


def _safe_container_use(nm_node, parent, kind):
    """Mirror of py2c's promotion safety check: True only for operations the
    unboxed typed form supports, so an injected annotation preserves behavior."""
    p = parent.get(nm_node)
    if p is None:
        return False
    if isinstance(p, ast.Assign) and nm_node in p.targets:
        return True
    if isinstance(p, ast.Subscript) and p.value is nm_node:
        sl = p.slice
        if isinstance(sl, ast.Slice):
            return False
        if isinstance(sl, ast.UnaryOp) and isinstance(sl.op, ast.USub):
            return False
        if isinstance(sl, ast.Constant) and isinstance(sl.value, int) \
                and sl.value < 0:
            return False
        return True
    if isinstance(p, ast.Attribute) and p.value is nm_node:
        gp = parent.get(p)
        methods = {"append"} if kind == "list" else set()
        return isinstance(gp, ast.Call) and gp.func is p and p.attr in methods
    if isinstance(p, ast.For) and p.iter is nm_node:
        return True
    if isinstance(p, ast.Compare) and nm_node in p.comparators and \
            any(isinstance(o, (ast.In, ast.NotIn)) for o in p.ops):
        return True
    if isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and \
            p.func.id == "len" and nm_node in p.args:
        return True
    return False


def _scope_nodes(tree):
    """Yield (scope_name, scope_node) for module + each function definition."""
    yield "module", tree
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n.name, n


def _clean_scalar(types):
    ts = [t for t in types if t != "None"]
    if len(ts) == 1 and ts[0] in SCALARS:
        return ts[0]
    return None


def _annotations_for(scope_name, scope_node, typemap):
    """Return [(assign_stmt, annotation_text), ...] safe to inject."""
    annotated = {n.target.id for n in ast.walk(scope_node)
                 if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    decls = {}
    for stmt in scope_node.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            kind = _empty_container_kind(stmt.value)
            if kind and stmt.targets[0].id not in annotated:
                decls.setdefault(stmt.targets[0].id, (kind, stmt))
    if not decls:
        return []
    assigns = {}
    for n in ast.walk(scope_node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in decls:
                    assigns[t.id] = assigns.get(t.id, 0) + 1
    parent = {}
    for n in ast.walk(scope_node):
        for c in ast.iter_child_nodes(n):
            parent[c] = n
    safe = {nm: True for nm in decls}
    for n in ast.walk(scope_node):
        if isinstance(n, ast.Name) and n.id in decls:
            if not _safe_container_use(n, parent, decls[n.id][0]):
                safe[n.id] = False
    result = []
    for nm, (kind, stmt) in decls.items():
        if not safe.get(nm) or assigns.get(nm, 0) != 1:
            continue
        obs = typemap.get("%s::%s" % (scope_name, nm))
        if not obs or obs.get("kind") != kind:
            continue
        if kind == "list":
            et = _clean_scalar(obs.get("elem", []))
            if et:
                result.append((stmt, "list[%s]" % et))
        elif kind == "dict":
            kt = _clean_scalar(obs.get("key", []))
            vt = _clean_scalar(obs.get("val", []))
            if kt and vt:
                result.append((stmt, "dict[%s, %s]" % (kt, vt)))
    return result


def generate_autotyped(src_path, typemap, out_path):
    """Rewrite empty containers in src to annotated form using observed types.
    Returns the number of containers annotated."""
    src = open(src_path, encoding="utf-8").read()
    tree = ast.parse(src)
    n_annot = 0
    for scope_name, scope_node in _scope_nodes(tree):
        for stmt, ann in _annotations_for(scope_name, scope_node, typemap):
            new = ast.AnnAssign(target=stmt.targets[0],
                                annotation=ast.Constant(value=ann),
                                value=stmt.value, simple=1)
            for i, s in enumerate(scope_node.body):
                if s is stmt:
                    scope_node.body[i] = new
                    n_annot += 1
                    break
    ast.fix_missing_locations(tree)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ast.unparse(tree))
    return n_annot


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def autotype(src_path, verbose=True):
    """Profile-and-rewrite `src_path`; return the path to compile.

    Best-effort: returns `src_path` unchanged on any failure or if nothing was
    annotated, so a broken/odd script just falls back to the normal path."""
    try:
        budget = int(os.environ.get("RPY_PROFILE_LOOP_BUDGET", _DEFAULT_BUDGET))
    except ValueError:
        budget = _DEFAULT_BUDGET
    try:
        src = open(src_path, encoding="utf-8").read()
        basename = os.path.basename(src_path)
        inst = instrument(src, budget)
        typemap = profile(inst, basename)
        if not typemap:
            return src_path
        out_dir = tempfile.mkdtemp(prefix="rpy_autotyped_")
        out_path = os.path.join(out_dir, basename)     # preserve module name
        n = generate_autotyped(src_path, typemap, out_path)
        if n <= 0:
            return src_path
        if verbose:
            print("  pgo: profiled %s, auto-typed %d container(s) -> %s" % (
                basename, n, out_path))
        return out_path
    except Exception as e:        # never break the build on a PGO hiccup
        if verbose:
            print("  pgo: skipped (%s)" % e)
        return src_path


if __name__ == "__main__":
    # Standalone: rpy_pgo.py <script.py>  -> prints the autotyped path.
    if len(sys.argv) < 2:
        print("usage: rpy_pgo.py <script.py>", file=sys.stderr)
        sys.exit(2)
    print(autotype(sys.argv[1]))
