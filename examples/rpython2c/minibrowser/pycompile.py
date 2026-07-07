#!/usr/bin/env python3
"""pycompile -- turn a page's python into minipy bytecode the browser can run.

Runs under CPython (like www2json): the browser shells out to it at page load.
It reads the `page.json` bundle, assembles one minipy program

    minidom prelude  +  document._register(...) for each id'd element
                     +  the page's <script type="python"> body

and compiles it to a `.mpyc` file. The native browser then `mpy_boot`s that
bytecode (defining the page's functions + the document/console/window globals)
and calls a handler by name whenever a button's onclick fires.

    python3 pycompile.py page.json minidom.py out.mpyc

Exits 0 on success (even with no page python -- the prelude alone still boots so
the DOM globals exist); non-zero only on a real compile error.
"""
import ast
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
# Find the minipy package: staged next to us (build dir) or in the repo tools/.
sys.path.insert(0, HERE)
_tools = os.path.join(ROOT, "tools")
if os.path.isdir(os.path.join(_tools, "minipy")):
    sys.path.insert(0, _tools)

import jitc     # noqa: E402  (JIT-compile <script type="rpython"> blocks)


# A minipy-subset ctypes shim, prepended when a page uses ctypes. It turns the
# rewritten calls (below) into the interpreter's FFI builtins: dlopen the JIT'd
# .so once, then dlsym + indirect-call per invocation.
_CTYPES_PRELUDE = '''\
def _ffi_open(path):
    return _native_dlopen(path)


def _ffi_call0(h, name):
    return _native_call0i(_native_dlsym(h, name))


def _ffi_call1(h, name, a):
    return _native_call1i(_native_dlsym(h, name), a)


def _ffi_call2(h, name, a, b):
    return _native_call2i(_native_dlsym(h, name), a, b)


def _ffi_call3(h, name, a, b, c):
    return _native_call3i(_native_dlsym(h, name), a, b, c)
'''


class _CtypesRewriter(ast.NodeTransformer):
    """Rewrite a page's ctypes use into the minipy FFI shim. minipy has no
    __getattr__, so `dll.calc_sum(1, 2)` can't dispatch on the handle at run
    time; we lower it at compile time instead:

        import ctypes                       -> (dropped)
        dll = ctypes.CDLL('/tmp/jit.foo.so')-> dll = _ffi_open('<cache>/jit.foo.so')
        dll.calc_sum(1, 2)                  -> _ffi_call2(dll, 'calc_sum', 1, 2)

    The .so path is redirected to this page's JIT cache dir. (The unrewritten
    source still runs under CPython with real ctypes -- see jit_test.py.)"""
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.handles = set()

    def visit_Import(self, node):
        node.names = [n for n in node.names if n.name != "ctypes"]
        return node if node.names else None

    def visit_Assign(self, node):
        v = node.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) \
                and v.func.attr == "CDLL" \
                and isinstance(v.func.value, ast.Name) \
                and v.func.value.id == "ctypes":
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self.handles.add(node.targets[0].id)
            path = v.args[0].value if v.args and \
                isinstance(v.args[0], ast.Constant) else ""
            newpath = os.path.join(self.cache_dir, os.path.basename(path))
            node.value = ast.Call(func=ast.Name(id="_ffi_open", ctx=ast.Load()),
                                  args=[ast.Constant(value=newpath)],
                                  keywords=[])
            return node
        return self.generic_visit(node)

    def visit_Call(self, node):
        self.generic_visit(node)
        f = node.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id in self.handles:
            helper = "_ffi_call%d" % len(node.args)
            return ast.Call(
                func=ast.Name(id=helper, ctx=ast.Load()),
                args=[ast.Name(id=f.value.id, ctx=ast.Load()),
                      ast.Constant(value=f.attr)] + node.args,
                keywords=[])
        return node


def _rewrite_ctypes(pysrc, cache_dir):
    tree = ast.parse(pysrc)
    tree = _CtypesRewriter(cache_dir).visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _lit(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("?")
    out.append('"')
    return "".join(out)




def _handler_name(onclick):
    """Map an inline handler like "foo()" to the function name "foo", or "" if
    it is not a simple name()-call we can bind to a script function."""
    s = onclick.strip()
    if not s.endswith(")"):
        return ""
    head = s[:s.find("(")].strip()
    if head and (head[0].isalpha() or head[0] == "_") and \
            all(c.isalnum() or c == "_" for c in head):
        return head
    return ""


def _emit_node(node, parent_var, counter, lines):
    tag = node.get("type", "")
    if tag.startswith("#"):            # #text and friends: fold as text, no node
        return
    counter[0] += 1
    var = "__e%d" % counter[0]
    lines.append("%s = document.createElement(%s)" % (var, _lit(tag)))
    text = node.get("text", "")
    if text:
        lines.append("%s.textContent = %s" % (var, _lit(text)))
    attrs = node.get("attributes", {})
    if attrs.get("id", ""):
        lines.append("%s.setAttribute(\"id\", %s)" % (var, _lit(attrs["id"])))
    if attrs.get("value", ""):
        lines.append("%s.value = %s" % (var, _lit(attrs["value"])))
    name = _handler_name(attrs.get("onclick", ""))
    if name:
        lines.append("%s.onclick = %s" % (var, name))
    lines.append("%s.appendChild(%s)" % (parent_var, var))
    for ch in node.get("children", []):
        _emit_node(ch, var, counter, lines)


def assemble(bundle, minidom_path):
    """One minipy program: minidom prelude + page script + a live body tree.

    The body is built with createElement/appendChild so it is the same mutable
    structure the script edits (not a separate read-only registration), and it
    comes *after* the script so onclick="foo()" can bind to the function foo.

    If the page ships <script type="rpython"> blocks, they are JIT-compiled to
    native .so files (jitc) and the page's ctypes use is rewritten onto the
    interpreter's FFI shim so `dll.calc_sum(1, 2)` runs the native code.
    """
    prelude = open(minidom_path).read()
    parts = [prelude]
    py = bundle.get("python", "")
    if bundle.get("rpython"):
        cache_dir, _results = jitc.compile_page(bundle, bundle.get("source", ""))
        py = _rewrite_ctypes(py, cache_dir)
        parts += ["", "# ---- ctypes -> FFI shim ----", _CTYPES_PRELUDE]
    if py.strip():
        parts += ["", "# ---- page <script type=\"python\"> ----", py]
    lines = ["", "# ---- live body built from the parsed page ----"]
    counter = [0]
    for ch in bundle.get("dom", {}).get("children", []):
        _emit_node(ch, "document.body", counter, lines)
    parts.append("\n".join(lines))
    return "\n".join(parts)


def main(argv):
    if len(argv) < 4:
        sys.stderr.write("usage: pycompile.py page.json minidom.py out.mpyc\n")
        return 2
    page_json, minidom_path, out_mpyc = argv[1], argv[2], argv[3]
    from minipy import compiler as C, mpyc

    with open(page_json) as fh:
        bundle = json.load(fh)
    program = assemble(bundle, minidom_path)
    prog = C.compile_source(program)
    with open(out_mpyc, "wb") as fh:
        fh.write(mpyc.encode(prog))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
