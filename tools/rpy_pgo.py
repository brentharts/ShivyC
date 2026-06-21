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

# The recorder lives in ONE shared module written next to the instrumented
# files; every instrumented file imports it, so a multi-file program records
# into a single dict and dumps it once (separate per-file copies would clobber
# the shared JSON). Captures, per `module::scope::name` key, the set of scalar
# element/key/value types observed.
_PROBE_MODULE = r'''
import json as __rpy_json, atexit as __rpy_atexit, os as __rpy_os
__RPY = {}
def scal(v):
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
def _add(lst, t):
    if t not in lst:
        lst.append(t)
def rec(key, val):
    e = __RPY.get(key)
    if e is None:
        e = __RPY[key] = {"kind": "", "elem": [], "key": [], "val": []}
    try:
        if isinstance(val, list):
            e["kind"] = "list"
            for x in val[:64]:
                _add(e["elem"], scal(x))
        elif isinstance(val, dict):
            e["kind"] = "dict"
            for k in list(val)[:64]:
                _add(e["key"], scal(k))
            for v in list(val.values())[:64]:
                _add(e["val"], scal(v))
        elif isinstance(val, set):
            e["kind"] = "set"
            for x in list(val)[:64]:
                _add(e["elem"], scal(x))
        else:
            e["kind"] = "scalar"
            _add(e["val"], scal(val))
    except Exception:
        pass
def _dump():
    try:
        p = __rpy_os.environ.get("RPY_TYPES_OUT", "/tmp/rpy_types.json")
        with open(p, "w") as f:
            __rpy_json.dump(__RPY, f)
    except Exception:
        pass
__rpy_atexit.register(_dump)
'''

# Prepended to each instrumented file: make the work dir importable (so a script
# run from elsewhere still finds the shared module) and bind the probe.
_SHIM = ("import os as __rpy_os, sys as __rpy_sys\n"
         "__rpy_sys.path.insert(0, __rpy_os.path.dirname("
         "__rpy_os.path.abspath(__file__)))\n"
         "from _rpy_probe import rec as __rpy_rec\n")

_MUTATORS = {"append", "add", "extend", "update", "insert",
             "setdefault", "__setitem__"}


# --------------------------------------------------------------------------
# 1. instrumentation
# --------------------------------------------------------------------------
class _Instrumenter:
    """Inject type probes and bound every loop. Operates on statement lists so
    it can insert probe statements as siblings after the statement they probe."""

    def __init__(self, budget, modstem):
        self.budget = budget
        self.modstem = modstem
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
                args=[ast.Constant(value="%s::%s::%s" % (
                          self.modstem, scope, nm)),
                      ast.Name(id=nm, ctx=ast.Load())],
                keywords=[]))
            out.append(call)
        return out


def instrument(src, modstem, budget=_DEFAULT_BUDGET):
    tree = ast.parse(src)
    tree = _Instrumenter(budget, modstem).run(tree)
    return _SHIM + "\n" + ast.unparse(tree)


# --------------------------------------------------------------------------
# 2. profiling run
# --------------------------------------------------------------------------
def _budget():
    try:
        return int(os.environ.get("RPY_PROFILE_LOOP_BUDGET", _DEFAULT_BUDGET))
    except ValueError:
        return _DEFAULT_BUDGET


def _modstem(path):
    return os.path.splitext(os.path.basename(path))[0]


def _has_main_guard(src):
    """True if the source has an `if __name__ == "__main__":` entry guard."""
    try:
        tree = ast.parse(src)
    except Exception:
        return False
    for n in tree.body:
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) \
                and isinstance(n.test.left, ast.Name) \
                and n.test.left.id == "__name__":
            return True
    return False


def profile_set(paths, timeout=20):
    """Instrument every path (module-qualified probes), run the entry script in
    a subprocess, and return the merged observed type map. {} on any failure.

    The entry is the first file with a `__main__` guard, else the first file;
    all instrumented files share one dir so imports resolve between them."""
    import json
    work = tempfile.mkdtemp(prefix="rpy_pgo_")
    types_out = os.path.join(work, "types.json")
    with open(os.path.join(work, "_rpy_probe.py"), "w",
              encoding="utf-8") as f:
        f.write(_PROBE_MODULE)
    entry = None
    budget = _budget()
    for p in paths:
        src = open(p, encoding="utf-8").read()
        with open(os.path.join(work, os.path.basename(p)), "w",
                  encoding="utf-8") as f:
            f.write(instrument(src, _modstem(p), budget))
        if entry is None and _has_main_guard(src):
            entry = os.path.basename(p)
    if entry is None:
        entry = os.path.basename(paths[0])
    try:
        env = dict(os.environ)
        env["RPY_TYPES_OUT"] = types_out
        # Strip flags so the profiling subprocess can't recurse into py2c PGO.
        for k in ("RPY_PROFILE_GENERATE", "RPY_PROFILE_USE", "RPY_PROFILE_OUT"):
            env.pop(k, None)
        subprocess.run([sys.executable, entry], cwd=work, env=env,
                       timeout=timeout, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
        if os.path.exists(types_out):
            with open(types_out, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _load_or_profile(paths, profile_in, profile_out):
    """Return a typemap: read from profile_in if given+present (no run), else
    profile the set and optionally cache it to profile_out."""
    import json
    if profile_in and os.path.exists(profile_in):
        try:
            with open(profile_in, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    typemap = profile_set(paths)
    if profile_out:
        try:
            with open(profile_out, "w", encoding="utf-8") as f:
                json.dump(typemap, f)
        except Exception:
            pass
    return typemap


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


# ---- static (no-run) type inference, used to fill containers the profiling
# run never reached. Mirrors py2c's static promotion inference. ------------
def _lit_type(node, locals_t):
    if isinstance(node, ast.Constant):
        v = node.value
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
    if isinstance(node, ast.Name):
        return locals_t.get(node.id, "obj")
    if isinstance(node, (ast.Compare, ast.BoolOp)):
        return "bool"
    if isinstance(node, ast.BinOp):
        lt = _lit_type(node.left, locals_t)
        rt = _lit_type(node.right, locals_t)
        if "str" in (lt, rt):
            return "str"
        if "float" in (lt, rt):
            return "float"
        return "int" if lt == "int" and rt == "int" else "obj"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = node.func.id
        if fn in ("int", "len", "ord", "abs", "hash"):
            return "int"
        if fn in ("str", "chr", "repr"):
            return "str"
        if fn == "float":
            return "float"
        if fn == "bool":
            return "bool"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "get" and len(node.args) == 2:
        return _lit_type(node.args[1], locals_t)
    return "obj"


def _iter_elem_type(node, locals_t):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id == "range":
        return "int"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "str"
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)) and node.elts:
        ts = {_lit_type(e, locals_t) for e in node.elts}
        ts.discard("None")
        if len(ts) == 1:
            return next(iter(ts))
    return "obj"


def _promo_val_type(name, node, locals_t):
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
            and node.value.id == name:
        return None
    if isinstance(node, ast.BinOp):
        cand = [t for t in (_promo_val_type(name, node.left, locals_t),
                            _promo_val_type(name, node.right, locals_t)) if t]
        if not cand:
            return "obj"
        if "str" in cand:
            return "str"
        if "float" in cand:
            return "float"
        return "int" if all(t == "int" for t in cand) else "obj"
    return _lit_type(node, locals_t)


def _static_ann(scope_node, name, kind):
    """Statically inferred annotation for container `name`, or None."""
    locals_t = {}
    for n in ast.walk(scope_node):
        if isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            et = _iter_elem_type(n.iter, locals_t)
            if et != "obj":
                locals_t[n.target.id] = et
    keys, vals = set(), set()
    for n in ast.walk(scope_node):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Subscript) and \
                        isinstance(tgt.value, ast.Name) and \
                        tgt.value.id == name:
                    if kind == "dict":
                        keys.add(_lit_type(tgt.slice, locals_t))
                    vt = _promo_val_type(name, n.value, locals_t)
                    if vt:
                        vals.add(vt)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == name and n.func.attr == "append" \
                and n.args:
            vt = _promo_val_type(name, n.args[0], locals_t)
            if vt:
                vals.add(vt)
    vt = _clean_scalar(list(vals))
    if not vt:
        return None
    if kind == "list":
        return "list[%s]" % vt
    kt = _clean_scalar(list(keys))
    return "dict[%s, %s]" % (kt, vt) if kt else None


def _annotations_for(modstem, scope_name, scope_node, typemap):
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
        obs = typemap.get("%s::%s::%s" % (modstem, scope_name, nm))
        observed = False        # did the run actually see any elements?
        if obs and obs.get("kind") == kind:
            if kind == "list":
                elem = obs.get("elem", [])
                if elem:
                    observed = True
                    et = _clean_scalar(elem)
                    if et:
                        result.append((stmt, "list[%s]" % et, "profiled"))
            else:
                ks, vs = obs.get("key", []), obs.get("val", [])
                if ks or vs:
                    observed = True
                    kt, vt = _clean_scalar(ks), _clean_scalar(vs)
                    if kt and vt:
                        result.append((stmt, "dict[%s, %s]" % (kt, vt),
                                       "profiled"))
        # If the run saw real elements its evidence is authoritative (clean ->
        # used above; mixed -> vetoed by falling through with nothing). Only when
        # the container was never populated at runtime do we consult static
        # inference, so a cold code path still gets typed when it provably can.
        if not observed:
            ann = _static_ann(scope_node, nm, kind)
            if ann:
                result.append((stmt, ann, "static"))
    return result


def generate_autotyped(src_path, modstem, typemap, out_path):
    """Rewrite empty containers in src to annotated form, merging profiled and
    static evidence. Returns (n_profiled, n_static)."""
    src = open(src_path, encoding="utf-8").read()
    tree = ast.parse(src)
    n_prof = n_stat = 0
    for scope_name, scope_node in _scope_nodes(tree):
        for stmt, ann, srclbl in _annotations_for(
                modstem, scope_name, scope_node, typemap):
            new = ast.AnnAssign(target=stmt.targets[0],
                                annotation=ast.Constant(value=ann),
                                value=stmt.value, simple=1)
            for i, s in enumerate(scope_node.body):
                if s is stmt:
                    scope_node.body[i] = new
                    if srclbl == "profiled":
                        n_prof += 1
                    else:
                        n_stat += 1
                    break
    ast.fix_missing_locations(tree)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ast.unparse(tree))
    return n_prof, n_stat


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def autotype(src_path, profile_in=None, profile_out=None, verbose=True):
    """Profile-and-rewrite a single script; return the path to compile.

    Best-effort: returns `src_path` unchanged on any failure or if nothing was
    annotated. With `profile_in` the cached profile is reused (no run); with
    `profile_out` a fresh profile is cached for later `-fprofile-use`."""
    try:
        typemap = _load_or_profile([src_path], profile_in, profile_out)
        out_dir = tempfile.mkdtemp(prefix="rpy_autotyped_")
        out_path = os.path.join(out_dir, os.path.basename(src_path))
        n_prof, n_stat = generate_autotyped(
            src_path, _modstem(src_path), typemap, out_path)
        if n_prof + n_stat <= 0:
            return src_path
        if verbose:
            print("  pgo: %s -> %d profiled + %d static container type(s) -> %s"
                  % (os.path.basename(src_path), n_prof, n_stat, out_path))
        return out_path
    except Exception as e:        # never break the build on a PGO hiccup
        if verbose:
            print("  pgo: skipped (%s)" % e)
        return src_path


def autotype_set(paths, profile_in=None, profile_out=None, verbose=True):
    """Profile a multi-file program once and return {orig_path: compile_path}.

    Every file is (re)written into one shared dir so cross-module imports keep
    resolving; files with no new annotations are copied through verbatim."""
    paths = list(paths)
    fallback = {p: p for p in paths}
    if not paths:
        return fallback
    try:
        typemap = _load_or_profile(paths, profile_in, profile_out)
        out_dir = tempfile.mkdtemp(prefix="rpy_autotyped_")
        mapping = {}
        total = 0
        for p in paths:
            op = os.path.join(out_dir, os.path.basename(p))
            n_prof, n_stat = generate_autotyped(p, _modstem(p), typemap, op)
            mapping[p] = op
            total += n_prof + n_stat
            if verbose and (n_prof or n_stat):
                print("  pgo: %s -> %d profiled + %d static type(s)" % (
                    os.path.basename(p), n_prof, n_stat))
        return mapping if total > 0 else fallback
    except Exception as e:
        if verbose:
            print("  pgo: skipped (%s)" % e)
        return fallback


if __name__ == "__main__":
    # Standalone: rpy_pgo.py <script.py> [more.py ...] -> prints the path(s).
    if len(sys.argv) < 2:
        print("usage: rpy_pgo.py <script.py> [more.py ...]", file=sys.stderr)
        sys.exit(2)
    if len(sys.argv) == 2:
        print(autotype(sys.argv[1]))
    else:
        for orig, comp in autotype_set(sys.argv[1:]).items():
            print("%s -> %s" % (orig, comp))
