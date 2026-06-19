"""Main executable for ShivyC compiler."""

import argparse
import pathlib
import platform
import subprocess
import sys

import shivyc.lexer as lexer
import shivyc.preproc as preproc

from shivyc.errors import error_collector, CompilerError
from shivyc.parser.parser import parse
from shivyc.il_gen import ILCode, SymbolTable, Context


def _concat_adjacent_strings(tokens):
    """Merge runs of adjacent string-literal tokens into single tokens.

    This implements C translation phase 6. It is especially important after
    macro expansion, where prefixes like `"[INFO] " fmt` become two adjacent
    string literals that must be treated as one.
    """
    import shivyc.token_kinds as token_kinds
    from shivyc.tokens import Token

    out = []
    i = 0
    n = len(tokens)
    while i < n:
        if tokens[i].kind == token_kinds.string:
            j = i + 1
            while j < n and tokens[j].kind == token_kinds.string:
                j += 1
            if j > i + 1:
                content = []
                for k in range(i, j):
                    chars = list(tokens[k].content)
                    # Drop each piece's trailing null; add a single one back.
                    if chars and chars[-1] == 0:
                        chars = chars[:-1]
                    content.extend(chars)
                content.append(0)
                rep = "".join(t.rep for t in tokens[i:j])
                r = tokens[i].r + tokens[j - 1].r
                out.append(Token(token_kinds.string, content, rep, r=r))
                i = j
                continue
        out.append(tokens[i])
        i += 1
    return out
from shivyc.asm_gen import ASMCode, ASMGen


def main():
    """Run the main compiler script."""

    if platform.system() != "Linux":
        err = "only x86_64 Linux is supported"
        print(CompilerError(err))
        return 1

    arguments = get_arguments()

    # When --musl is given, materialize the packaged musl headers and put their
    # include directories (and required defines) ahead of the user's, so that
    # #include resolves against musl instead of the host glibc. This keeps the
    # build self-contained: no musl tree needs to exist in the source checkout.
    include_dirs = list(getattr(arguments, "include_dirs", []))
    defines = list(getattr(arguments, "defines", []))
    if getattr(arguments, "use_musl", False):
        from shivyc import musl as _musl
        _tree = _musl.materialize(getattr(arguments, "musl_dir", None))
        defines = _tree.defines() + defines
        include_dirs = _tree.public_include_dirs() + include_dirs

    # Apply any -I include directories to the preprocessor.
    preproc.set_include_dirs(include_dirs)
    preproc.set_defines(defines)

    # Whether to alias long double to double (-f-long-double-as-double).
    import shivyc.ctypes as ctypes
    ctypes.long_double_as_double = getattr(
        arguments, "long_double_as_double", False)

    # Load a per-function register budget (thread partitioning) if supplied;
    # ASMGen consults arguments._thread_alloc to restrict allocation.
    arguments._thread_alloc = None
    if getattr(arguments, "thread_alloc_json", None):
        import json
        try:
            with open(arguments.thread_alloc_json) as fh:
                arguments._thread_alloc = json.load(fh)
        except OSError as e:
            print(CompilerError(f"cannot read thread budget: {e}"))
            return 1

    # Whole-program memory-safety analysis: detect use-after-free / double-free
    # (and optionally auto-free candidates) over all inputs, then exit.
    if getattr(arguments, "check_memory", False):
        import shivyc.memory_safety as memory_safety
        return memory_safety.run(arguments.files, arguments)

    if getattr(arguments, "pdf", None):
        import shivyc.pdf_report as pdf_report
        return pdf_report.run(arguments.files, arguments, arguments.pdf)

    # Micro-slicing analysis: find pure, independent fragments and a slice plan
    # for productive spin-waiting, then exit.
    if getattr(arguments, "microslice", False):
        import shivyc.microslice as microslice
        return microslice.run(arguments.files, arguments)

    # Thread-partition switcher: analyze threads.left/right across the inputs
    # and emit a specialized context switcher, then exit. (Analysis + codegen
    # of the switcher only; the user's TUs are compiled normally otherwise.)
    if getattr(arguments, "emit_thread_switcher", None):
        import shivyc.thread_contracts as thread_contracts
        return thread_contracts.run(arguments.files, arguments)

    # Whole-program call-graph report: build the cross-TU call graph over all
    # input files and print it, then exit. (Analysis only; emits no objects.)
    if getattr(arguments, "print_call_graph", False):
        import shivyc.callgraph as callgraph
        graph, ok = callgraph.build_program_graph(arguments.files, arguments)
        error_collector.show()
        print(graph.summary())
        undef = graph.undefined_calls()
        externals = sorted({c for s in undef.values() for c in s})
        if externals:
            print("external/undefined callees: " + ", ".join(externals))
        return 0 if ok else 1

    # The metamorphic-reentrancy and -O4 near-scratch safety checks reason
    # about the call graph. Compiled per file, they would only see one TU and
    # miss recursion (or address-taking) that travels through another unit.
    # When either feature is active and there is more than one TU, build the
    # whole-program graph up front so those checks can close cross-TU cycles.
    arguments._wp_graph = None
    arguments._simd_pack_layout = None
    arguments._inline_bodies = None

    # Whole-program elimination of never-accessed struct members.
    import shivyc.member_elim as member_elim
    member_elim.enabled = getattr(arguments, "eliminate_unused_members", False)
    member_elim.install({})

    needs_graph = (getattr(arguments, "metamorphic", False)
                   or getattr(arguments, "opt_level", 0) >= 4
                   or getattr(arguments, "simd_pack_globals", False))
    c_files = [f for f in arguments.files if f.endswith(".c")]
    if needs_graph and len(c_files) > 1:
        import shivyc.callgraph as callgraph
        graph, _ = callgraph.build_program_graph(arguments.files, arguments)
        arguments._wp_graph = graph
        # Promote externally-linked flag globals into a single, consistent
        # xmm15 layout shared by every translation unit. This is only sound
        # with the whole-program view: the bit assignment must agree across
        # units, and a flag whose address escapes anywhere must be excluded.
        if getattr(arguments, "simd_pack_globals", False):
            arguments._simd_pack_layout = graph.simd_pack_layout()
        # At -O4, inline small pure leaf functions across TU boundaries: their
        # bodies were captured while building the graph, and a single unit
        # never has the body of a callee defined in another file.
        if getattr(arguments, "opt_level", 0) >= 4:
            arguments._inline_bodies = graph.inlinable
        # Building the graph runs its own front end; discard any diagnostics it
        # accumulated so the real compile below starts from a clean slate.
        error_collector.clear()

    # Whole-program unused-member analysis: parse + type every translation
    # unit with the collector active, then compute which struct members are
    # safe to drop. The per-file compile below consults the result.
    if member_elim.enabled and c_files:
        import shivyc.callgraph as callgraph
        member_elim.begin_collection()
        callgraph.build_program_graph(arguments.files, arguments)
        mapping = member_elim.finalize()
        member_elim.install(mapping)
        error_collector.clear()
        if getattr(arguments, "print_eliminated_members", False):
            for tag in sorted(mapping):
                members = ", ".join(sorted(mapping[tag]))
                print(f"eliminated from 'struct {tag}': {members}")

    objs = []
    arguments._extra_objs = []
    for file in arguments.files:
        objs.append(process_file(file, arguments))
    objs.extend(arguments._extra_objs)

    error_collector.show()
    if any(not obj for obj in objs):
        return 1

    # -c: compile and assemble only, leaving the .o files; do not link.
    if getattr(arguments, "compile_only", False):
        if (arguments.output_name and len(arguments.output_name) == 1
                and len(objs) == 1 and objs[0] != arguments.output_name[0]):
            import shutil
            shutil.move(objs[0], arguments.output_name[0])
        return 0

    if True:
        # set the output ELF name
        out = "out"
        if arguments.output_name is not None and \
                len(arguments.output_name) == 1:
            # set the output ELF name
            out = arguments.output_name[0]
        writable_text = (getattr(arguments, "metamorphic", False)
                         or getattr(arguments, "opt_level", 0) >= 4)
        if not link(out, objs, writable_text):
            err = "linker returned non-zero status"
            print(CompilerError(err))
            return 1
        return 0


def process_file(file, args):
    """Process single file into object file and return the object file name."""
    if file[-2:] == ".c":
        return process_c_file(file, args)
    elif file[-3:] == ".py":
        return process_py_file(file, args)
    elif file[-2:] == ".o":
        return file
    else:
        err = f"unknown file type: '{file}'"
        error_collector.add(CompilerError(err))
        return None


def process_py_file(file, args):
    """Transpile an rpython `.py` to C with tools/py2c.py, then compile it.

    This lets ShivyCX consume rpython sources directly --
    `shivyc.main kernels.py harness.c -o run` -- so the numpy examples no longer
    need a hand-written .c copy or a transpile-then-compile shell script. Any
    runtime support code py2c needs (shivyc_rt.c) is generated here and queued
    for linking; pure kernels (the SIMD examples) reference no runtime, so the
    runtime include is dropped and the kernel C compiles on its own.
    """
    import os
    import sys
    import re
    import tempfile

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(repo_root, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import py2c
    except Exception as e:                       # pragma: no cover
        error_collector.add(CompilerError(f"cannot import py2c: {e}"))
        return None

    out_dir = tempfile.mkdtemp(prefix="shivyc_py2c_")
    try:
        out_c, err = py2c.transpile_file(file, out_dir)
    except Exception as e:
        error_collector.add(CompilerError(f"py2c failed on '{file}': {e}"))
        return None
    if err or not out_c:
        error_collector.add(
            CompilerError(f"py2c could not translate '{file}': {err}"))
        return None

    code = open(out_c, encoding="utf-8").read()
    uses_rt = re.search(
        r"\b(obj_[a-z]|OBJ_[A-Z]|aalloc|afree|pystr|subscript|truthy|"
        r"list_of|make_closure|pyconcat)", code)
    if uses_rt:
        # Module needs the runtime: emit and queue shivyc_rt.c for linking.
        py2c.write_runtime(out_dir)
        rt_c = os.path.join(out_dir, "shivyc_rt.c")
        if os.path.exists(rt_c):
            rt_obj = process_c_file(rt_c, args)
            if rt_obj:
                getattr(args, "_extra_objs", []).append(rt_obj)
    else:
        # Pure kernel/program: drop the (unused) runtime include so the
        # C11-subset front end compiles it directly, but re-supply the handful
        # of libc prototypes the dropped header would have provided.
        code = re.sub(r'#include "shivyc_rt\.h"\n', "", code)
        prelude = []
        if re.search(r"\bmalloc\b", code):
            prelude.append("void *malloc(unsigned long);")
        if re.search(r"\bfree\b", code):
            prelude.append("void free(void *);")
        if re.search(r"\brealloc\b", code):
            prelude.append("void *realloc(void *, unsigned long);")
        if re.search(r"\bprintf\b", code):
            prelude.append("int printf(const char *, ...);")
        if re.search(r"\bsqrt\b", code):
            prelude.append("double sqrt(double);")
        for fn in ("cbrt", "exp", "exp2", "expm1", "log", "log2", "log10",
                   "log1p", "sin", "cos", "tan", "asin", "acos", "atan",
                   "sinh", "cosh", "tanh", "asinh", "acosh", "atanh", "fabs",
                   "floor", "ceil", "round", "trunc"):
            if re.search(r"\b" + fn + r"\b", code):
                prelude.append("double %s(double);" % fn)
        for fn in ("pow", "fmod", "fmax", "fmin", "atan2", "hypot",
                   "copysign"):
            if re.search(r"\b" + fn + r"\b", code):
                prelude.append("double %s(double, double);" % fn)
        if re.search(r"\batoi\b", code):
            prelude.append("int atoi(const char *);")
        for sym, proto in [
                ("fopen", "void *fopen(const char *, const char *);"),
                ("fputs", "int fputs(const char *, void *);"),
                ("fgets", "char *fgets(char *, int, void *);"),
                ("fwrite", "unsigned long fwrite(const void *, unsigned long, unsigned long, void *);"),
                ("fclose", "int fclose(void *);"),
                ("system", "int system(const char *);"),
                ("puts", "int puts(const char *);"),
                ("strlen", "unsigned long strlen(const char *);"),
                ("strcmp", "int strcmp(const char *, const char *);"),
                ("fork", "int fork(void);"),
                ("_exit", "void _exit(int);"),
                ("waitpid", "int waitpid(int, int *, int);")]:
            if re.search(r"\b" + sym + r"\b", code):
                prelude.append(proto)
        for stream in ("stdin", "stdout", "stderr"):
            if re.search(r"\b" + stream + r"\b", code):
                prelude.append("extern void *" + stream + ";")
        if prelude:
            code = "\n".join(prelude) + "\n" + code
        with open(out_c, "w", encoding="utf-8") as f:
            f.write(code)

    return process_c_file(out_c, args)


def process_c_file(file, args):
    """Compile a C file into an object file and return the object file name."""
    code = read_file(file)
    if not error_collector.ok():
        return None

    # Language-extension pre-pass: recognize __stackless__/__metamorphic__
    # specifiers and assert-style contracts, strip them to plain C, and record
    # the metadata for later passes.
    import shivyc.extensions as extensions
    try:
        code, ext_info = extensions.preprocess_extensions(code)
    except extensions.ExtensionError as e:
        error_collector.add(CompilerError(str(e)))
        return None
    args._extensions = ext_info

    token_list = lexer.tokenize(code, file)
    if not error_collector.ok():
        return None

    token_list = preproc.process(token_list, file)
    if not error_collector.ok():
        return None

    # Any `unrecognized` token that survived preprocessing is in live code
    # (dead #if branches and #error message text were dropped/consumed by the
    # preprocessor). Report it now with the original lexical diagnostic.
    import shivyc.token_kinds as _tk
    for tok in token_list:
        if tok.kind == _tk.unrecognized:
            error_collector.add(CompilerError(
                "unrecognized token at '%s'" % tok.content, tok.r))
    if not error_collector.ok():
        return None

    # Extract GCC alias/weak attributes (and strip other attributes) at the
    # token level; the recorded aliases become .weak/.set directives below.
    import shivyc.weak_alias as weak_alias
    token_list, aliases = weak_alias.extract_aliases(token_list)

    # Translation phase 6: concatenate adjacent string literals
    # (e.g. `"[INFO] " fmt` after macro expansion becomes one literal).
    token_list = _concat_adjacent_strings(token_list)

    # If parse() can salvage the input into a parse tree, it may emit an
    # ast_root even when there are errors saved to the error_collector. In this
    # case, we still want to continue the compiler stages.
    #
    # Parsing depends only on the token stream, so consult the on-disk AST
    # cache (keyed by a hash of these tokens) before parsing from scratch.
    import shivyc.cache as cache
    use_cache = not getattr(args, "no_cache", False)
    cache_key = cache.token_key(token_list) if use_cache else None
    ast_root = cache.load_ast(cache_key) if cache_key else None
    if ast_root is None:
        ast_root = parse(token_list)
        if cache_key is not None and ast_root is not None \
                and error_collector.ok():
            cache.store_ast(cache_key, ast_root)
    if not ast_root:
        return None

    il_code = ILCode()
    symbol_table = SymbolTable()
    import shivyc.contracts as contracts
    contracts.reset_unit()
    contracts.install_contracts(getattr(args._extensions, "contracts", None))
    ast_root.make_il(il_code, symbol_table, Context())
    if not error_collector.ok():
        return None

    # Reject 80-bit long double, but only where it actually reaches the
    # generated program. Unused `static inline` long double helpers (musl's
    # headers define several) are removed by dead-function elimination first,
    # so merely including such a header does not fail; a long double object or
    # computation that survives into a real function is rejected.
    if il_code.long_double_taint:
        import shivyc.dce as dce
        from shivyc.tree.general_nodes import LONG_DOUBLE_MSG
        dce.eliminate_dead_functions(il_code, symbol_table)
        for fn, rng in il_code.long_double_taint.items():
            if fn in il_code.commands:
                error_collector.add(CompilerError(LONG_DOUBLE_MSG, rng))
        if not error_collector.ok():
            return None

    ext_info = getattr(args, "_extensions", None)

    # Auto-free: insert free() for provably-local, non-escaping, never-freed
    # allocations so the programmer may omit it. Runs before the optimization
    # passes so the inserted frees are optimized like any other call.
    if getattr(args, "auto_free", False):
        import shivyc.memory_safety as memory_safety
        try:
            n = memory_safety.insert_auto_frees(il_code, symbol_table, args)
            if n and not getattr(args, "quiet", False):
                print(f"auto-free: inserted {n} free(s) for non-escaping "
                      f"allocations in {file}")
        except Exception:
            pass  # never let the analysis break a normal compile

    # Cross-TU inlining runs first, before any optimization pass: splicing a
    # small pure leaf (whose body was captured from the whole-program graph)
    # into its direct call sites removes the call, so the later passes (tail
    # calls, near-scratch, recursion checks) see the simplified, call-free
    # code. Doing it after tail-call lowering would be wrong -- a `return f(x)`
    # is turned into a tail jump that drops the Return the inlined body needs.
    inline_bodies = getattr(args, "_inline_bodies", None)
    if inline_bodies:
        import shivyc.inline as inline
        import shivyc.stackless as _stk
        for fn in il_code.commands:
            cmds = _stk._apply_direct_calls(il_code.commands[fn], symbol_table)
            cmds, _ = inline.inline_calls(cmds, inline_bodies, il_code)
            il_code.commands[fn] = cmds
        # Inlining often leaves a static helper with no remaining callers.
        # Drop any internal-linkage function that is now unreachable.
        import shivyc.dce as dce
        dce.eliminate_dead_functions(il_code, symbol_table)

    # Metamorphic returns (advanced/experimental): functions marked
    # __metamorphic__ return via a self-modified slot in a writable, executable
    # section instead of the stack. Only active when -fmetamorphic is passed.
    metamorphic_funcs = set()
    if getattr(args, "metamorphic", False) and ext_info:
        metamorphic_funcs = {name for name in ext_info.attrs
                             if ext_info.has_attr(name, "metamorphic")}
    il_code.metamorphic_funcs = metamorphic_funcs

    # Stackless lowering applies whole-program (-fstackless-calls / -O4) or
    # per-function via the __stackless__ specifier.
    stackless_attr_funcs = set()
    if ext_info:
        stackless_attr_funcs = {
            name for name in ext_info.attrs
            if ext_info.has_attr(name, "stackless")}

    whole_program = (getattr(args, "stackless_calls", False)
                     or getattr(args, "opt_level", 0) >= 4)

    if whole_program or stackless_attr_funcs or metamorphic_funcs:
        import shivyc.stackless as stackless
        if whole_program:
            enabled = None
        else:
            # Even without whole-program stackless, metamorphic calls need
            # their target names resolved (direct-call folding).
            enabled = stackless_attr_funcs | metamorphic_funcs
        # A call to a metamorphic function returns to its call site, so it must
        # never be turned into a tail jump (which would drop the return).
        stackless.optimize(il_code, symbol_table, enabled,
                            no_tail=metamorphic_funcs)

        # Metamorphic call sites need their target name resolved even in callers
        # the stackless pass did not otherwise optimize.
        if metamorphic_funcs:
            for fn in il_code.commands:
                il_code.commands[fn] = stackless._apply_direct_calls(
                    il_code.commands[fn], symbol_table)

            # A metamorphic function uses a single static return slot, so it
            # cannot be safely re-entered. Refuse if any is reachable from
            # itself through the (direct) call graph, rather than emit code
            # that would corrupt the return slot at run time.
            import shivyc.il_cmds.control as _control
            edges = {}
            for fn, cmds in il_code.commands.items():
                edges[fn] = {c.direct_name for c in cmds
                             if isinstance(c, _control.Call) and c.direct_name}
            # Close the graph across translation units: for functions defined
            # in *other* units, fold in the whole-program edges so recursion
            # that travels through another TU is detected. This TU's own edges
            # are kept as computed locally (they reflect tail-call lowering),
            # so a single-file build is unaffected.
            wp = getattr(args, "_wp_graph", None)
            if wp is not None:
                for fn, callees in wp.edges.items():
                    if fn not in il_code.commands:
                        edges.setdefault(fn, set()).update(callees)
            for m in metamorphic_funcs:
                seen, stack = set(), list(edges.get(m, ()))
                while stack:
                    cur = stack.pop()
                    if cur == m:
                        err = (f"metamorphic function '{m}' may be re-entered "
                               f"(recursion); not supported")
                        error_collector.add(CompilerError(err))
                        return None
                    if cur in seen:
                        continue
                    seen.add(cur)
                    stack.extend(edges.get(cur, ()))

    # Contract-driven SIMD: prove array-length contracts across the call graph
    # and, where proven, license a fallback-free SIMD reduction.
    if ext_info and ext_info.contracts:
        import shivyc.simd_contracts as simd_contracts
        proven, reports = simd_contracts.analyze(
            il_code, symbol_table, ext_info)
        for report in reports:
            print(report)
        il_code.simd_proven = proven

    # Argument packing (-f-pack-args): a non-standard calling convention that
    # bit-packs small integer parameters into shared argument registers,
    # reducing register pressure and stack spills (most visibly across deeply
    # nested calls). Direct calls are resolved first so each statically-known
    # callee can be named; the pass then rewrites packable callee prologues and
    # annotates their direct call sites.
    if getattr(args, "pack_args", False):
        import shivyc.stackless as _stk
        import shivyc.pack_args as _pack_args
        for fn in il_code.commands:
            il_code.commands[fn] = _stk._apply_direct_calls(
                il_code.commands[fn], symbol_table)
        _pack_args.optimize(il_code, symbol_table)

    # Loop register-promotion for packed globals (-fsimd-pack-globals): hoist
    # the xmm15 decompress/recompress of packed globals out of loops, caching
    # the live value in a GP register across the loop body.
    if getattr(args, "simd_pack_globals", False):
        import shivyc.simd_pack_promote as _promote
        _promote.optimize(il_code, symbol_table)

    # -O4 near-function scratch: a non-reentrant function can hold its locals
    # and register spills in a static per-function buffer instead of the stack,
    # cutting stack pressure (and, for leaf functions, the frame entirely). It
    # is only safe for functions that cannot be active twice at once, so we
    # require: not reachable from itself through the (direct) call graph, and
    # not address-taken (which could allow indirect re-entry).
    if getattr(args, "opt_level", 0) >= 4:
        import shivyc.il_cmds.control as _ctrl
        import shivyc.il_cmds.value as _val
        names_by_val = {v: n for v, n in symbol_table.names.items()
                        if getattr(v, "ctype", None) is not None
                        and v.ctype.is_function()}
        edges = {}
        addr_taken = set()
        for fn, cmds in il_code.commands.items():
            e = set()
            for c in cmds:
                if isinstance(c, _ctrl.Call) and c.direct_name:
                    e.add(c.direct_name)
                if isinstance(c, _val.AddrOf) and c.var in names_by_val:
                    addr_taken.add(names_by_val[c.var])
            edges[fn] = e

        # Close the graph across translation units (see the metamorphic check
        # above): add edges from functions defined in other units, and treat a
        # function whose address is taken in *any* unit as address-taken. For a
        # single-file build the whole-program graph equals this TU, so neither
        # addition changes anything.
        wp = getattr(args, "_wp_graph", None)
        if wp is not None:
            for fn, callees in wp.edges.items():
                if fn not in il_code.commands:
                    edges.setdefault(fn, set()).update(callees)
            addr_taken |= wp.addr_taken

        # A function defined somewhere we can analyze has known call edges; a
        # function we cannot see (declared-only in this TU, or external to the
        # whole program) is "unknown" and might call back into us. In a single
        # TU, only this unit's functions are known, so a call to a function
        # defined in another file is unknown; with the whole-program graph,
        # every function defined anywhere in the program becomes known. This is
        # what lets whole-program analysis grant near-scratch that a sound
        # single-TU analysis must refuse.
        known = set(il_code.commands)
        if wp is not None:
            known |= wp.defined

        # Functions with internal (static) linkage cannot be named by code
        # outside their own translation unit, so no unknown external can
        # re-enter them by name; only a cycle through known functions can.
        internal_funcs = {
            symbol_table.names[v]
            for v, lk in symbol_table.linkage_type.items()
            if lk == symbol_table.INTERNAL and v in symbol_table.names
            and getattr(v, "ctype", None) is not None and v.ctype.is_function()}

        def _eligible(fn):
            # Walk fn's transitive callees. fn can be re-entered (so it is not
            # eligible) if either:
            #   (a) there is a cycle back to fn through known functions, or
            #   (b) fn's closure reaches an unknown external that could name
            #       fn -- conservatively, any unknown external when fn has
            #       external linkage.
            seen, stack = set(), list(edges.get(fn, ()))
            hits_unknown = False
            while stack:
                cur = stack.pop()
                if cur == fn:
                    return False                   # (a) recursion
                if cur in seen:
                    continue
                seen.add(cur)
                if cur not in known:
                    hits_unknown = True            # external: do not expand
                    continue
                stack.extend(edges.get(cur, ()))
            if hits_unknown and fn not in internal_funcs:
                return False                       # (b) external may re-enter
            return True

        il_code.near_scratch_funcs = {
            fn for fn in il_code.commands
            if fn not in addr_taken and _eligible(fn)}

    asm_code = ASMCode()
    ASMGen(il_code, symbol_table, asm_code, args).make_asm()

    # Emit recorded weak aliases as assembler directives.
    for alias_name, target, is_weak in aliases:
        if is_weak:
            asm_code.add_weak(alias_name)
        asm_code.add_alias(alias_name, target)

    asm_source = asm_code.full_code()
    if not error_collector.ok():
        return None

    asm_file = file[:-2] + ".s"
    obj_file = file[:-2] + ".o"

    write_asm(asm_source, asm_file)
    if not error_collector.ok():
        return None

    assemble(asm_file, obj_file)
    if not error_collector.ok():
        return None

    return obj_file


def get_arguments():
    """Get the command-line arguments.

    This function sets up the argument parser. Returns a tuple containing
    an object storing the argument values and a list of the file names
    provided on command line.
    """
    desc = """Compile, assemble, and link C files. Option flags starting
    with `-z` are primarily for debugging or diagnostic purposes."""
    parser = argparse.ArgumentParser(
        prog='ShivyC',
        description=desc,
        usage="shivyc [-h] [options] files...")

    # Files to compile
    parser.add_argument("files", metavar="files", nargs="+")

    # Boolean flag for whether to print register allocator performance info
    parser.add_argument("-z-reg-alloc-perf",
                        help="display register allocator performance info",
                        dest="show_reg_alloc_perf", action="store_true")

    # Pack small (1-8 bit) static global flags into the last SIMD register
    # (xmm15) for zero-latency reads in hot / interrupt routines.
    parser.add_argument("-fsimd-pack-globals",
                        help="pack small global flags into xmm15",
                        dest="simd_pack_globals", action="store_true")

    # Lower deeply-nested calls with direct calls, tail-call jumps, and
    # frame-pointer omission to cut call overhead.
    parser.add_argument("-fstackless-calls",
                        help="direct calls, tail-call jmps, frameless funcs",
                        dest="stackless_calls", action="store_true")

    # Argument packing: a non-standard calling convention that bit-packs several
    # small integer parameters into as few argument registers as possible.
    # Applied to statically-known (direct) calls of qualifying functions only.
    parser.add_argument("-f-pack-args",
                        help="pack small integer args into shared registers",
                        dest="pack_args", action="store_true")

    # Advanced/experimental: metamorphic returns. Requires a writable text
    # segment (the linker is told to make it writable). The return address is
    # patched into the callee's code by the caller, so no return address is
    # pushed. Enable per-function with the __metamorphic__ specifier.
    parser.add_argument("-fmetamorphic",
                        help="enable metamorphic returns (writable .text)",
                        dest="metamorphic", action="store_true")

    parser.add_argument("-c",
                        help="compile and assemble to .o, but do not link",
                        dest="compile_only", action="store_true")

    parser.add_argument(
        "-f-eliminate-unused-members",
        help="whole-program: remove struct members never accessed in any "
             "translation unit (only when provably safe)",
        dest="eliminate_unused_members", action="store_true")

    parser.add_argument(
        "--print-eliminated-members",
        help="report struct members removed by -f-eliminate-unused-members",
        dest="print_eliminated_members", action="store_true")

    parser.add_argument(
        "-f-long-double-as-double",
        help="treat 'long double' as 64-bit double (with a warning); this "
             "compiler never supports 80-bit floats",
        dest="long_double_as_double", action="store_true")

    # Optimization level. -O4 is aggressive and, like -fmetamorphic, depends on
    # a writable text segment; it turns on whole-program stackless lowering and
    # near-function scratch storage to reduce stack pressure.
    parser.add_argument("-O", type=int, default=0,
                        help="optimization level (0-4); 4 needs writable .text",
                        dest="opt_level")
    # Generate binary file with file name
    parser.add_argument(
        "-o",
        nargs=1,
        metavar="file",
        help="place output into <file>",
        dest="output_name")

    # Additional directories searched for #include files.
    parser.add_argument("-I", metavar="dir", dest="include_dirs",
                        action="append", default=[],
                        help="add a directory to the include search path")

    # Predefine a macro (NAME or NAME=VALUE), like the C compiler's -D.
    parser.add_argument("-D", metavar="name[=value]", dest="defines",
                        action="append", default=[],
                        help="predefine a preprocessor macro")

    # Compile against the packaged musl libc (bypassing glibc). Materializes
    # musl's headers to a temp dir and prepends their include paths + defines,
    # so user code resolves #include against musl. The needed musl .c sources
    # can then be extracted/compiled on demand (see shivyc.musl).
    parser.add_argument("--musl", dest="use_musl", action="store_true",
                        help="compile against the packaged musl libc, not glibc")
    parser.add_argument("--musl-dir", dest="musl_dir", default=None,
                        help="where to materialize musl headers/sources "
                             "(default: a temp directory)")

    # Build and print the whole-program (cross-TU) call graph, then exit.
    parser.add_argument("--print-call-graph", dest="print_call_graph",
                        action="store_true",
                        help="print the cross-translation-unit call graph")

    # Register-partitioned threads (bare-metal).
    parser.add_argument("--emit-thread-switcher", dest="emit_thread_switcher",
                        metavar="OUT.s", default=None,
                        help="analyze threads.left/right declarations across "
                             "the inputs and write a specialized context "
                             "switcher to OUT.s, then exit")
    parser.add_argument("--thread-alloc-json", dest="thread_alloc_json",
                        metavar="FILE", default=None,
                        help="constrain each function's register pool to the "
                             "{func: [reg,...]} budget in FILE (used by the "
                             "thread partitioner to make footprints disjoint)")

    # Disable the on-disk AST parse cache.
    parser.add_argument("--no-cache", dest="no_cache", action="store_true",
                        help="disable the on-disk parsed-AST cache")

    # Whole-program memory-safety analysis.
    parser.add_argument("--check-memory", dest="check_memory",
                        action="store_true",
                        help="run the whole-program use-after-free / double-free "
                             "analysis over the inputs and exit")
    parser.add_argument("--auto-free", dest="auto_free", action="store_true",
                        help="with --check-memory, also report (and where safe, "
                             "insert) automatic frees for non-escaping allocations")

    # Micro-slicing: productive spin-waiting analysis.
    parser.add_argument("--microslice", dest="microslice", action="store_true",
                        help="analyze the inputs for pure, independent fragments "
                             "and print a slice plan for productive spinning")
    parser.add_argument("--slice-budget", dest="slice_budget", type=int,
                        metavar="N", default=None,
                        help="per-slice cost budget for --microslice (default 64)")
    parser.add_argument("--emit-microslice", dest="emit_microslice",
                        metavar="OUT.c", default=None,
                        help="with --microslice, write a work-injected acquire "
                             "scaffold for the hottest fragment to OUT.c")

    parser.add_argument("--pdf", dest="pdf", nargs="?", const="/tmp",
                        default=None, metavar="DIR",
                        help="generate a LaTeX/PDF build report (overview, "
                             "per-module sections, safety findings in red, a "
                             "TikZ call graph, and the run output in an "
                             "appendix). Output directory defaults to /tmp.")

    return parser.parse_args()


def read_file(file):
    """Return the contents of the given file."""
    try:
        with open(file) as c_file:
            return c_file.read()
    except IOError:
        descrip = f"could not read file: '{file}'"
        error_collector.add(CompilerError(descrip))


def write_asm(asm_source, asm_filename):
    """Save the given assembly source to disk at asm_filename.

    asm_source (str) - Full assembly source code.
    asm_filename (str) - Filename to which to save the generated assembly.

    """
    try:
        with open(asm_filename, "w") as s_file:
            s_file.write(asm_source)
    except IOError:
        descrip = f"could not write output file '{asm_filename}'"
        error_collector.add(CompilerError(descrip))


def assemble(asm_name, obj_name):
    """Assemble the given assembly file into an object file."""
    try:
        subprocess.check_call(["as", "-o", obj_name, asm_name])
        return True
    except subprocess.CalledProcessError:
        err = "assembler returned non-zero status"
        error_collector.add(CompilerError(err))
        return False


def link(binary_name, obj_names, writable_text=False):
    """Assemble the given object files into a binary.

    When `writable_text` is set (for -fmetamorphic / -O4), the linker is asked
    to emit a writable, non-page-aligned text segment via `-N` (OMAGIC), so
    self-modifying metamorphic-return code can patch instruction bytes at run
    time. This is intentionally unsafe and opt-in.
    """

    try:
        crtnum = find_crtnum()
        if not crtnum: return

        crti = find_library_or_err("crti.o")
        if not crti: return

        linux_so = find_library_or_err("ld-linux-x86-64.so.2")
        if not linux_so: return

        crtn = find_library_or_err("crtn.o")
        if not crtn: return

        cmd = ["ld"]
        # Writable text for metamorphic returns is arranged via the .text
        # section's "awx" flag (set in asm_gen), which is compatible with the
        # glibc crt startup; the older -N/OMAGIC route is not.
        cmd += ["-dynamic-linker", linux_so, crtnum, crti, "-lc", "-lm"]

        # find files to link
        subprocess.check_call(cmd + obj_names + [crtn, "-o", binary_name])

        return True

    except subprocess.CalledProcessError:
        return False


def find_crtnum():
    """Search for the crt0, crt1, or crt2.o files on the system.

    If one is found, return its path. Else, add an error to the
    error_collector and return None.
    """
    for file in ["crt2.o", "crt1.o", "crt0.o"]:
        crt = find_library(file)
        if crt: return crt

    err = "could not find crt0.o, crt1.o, or crt2.o for linking"
    error_collector.add(CompilerError(err))
    return None


def find_library_or_err(file):
    """Search the given library file and return path if found.

    If not found, add an error to the error collector and return None.
    """
    path = find_library(file)
    if not path:
        err = f"could not find {file}"
        error_collector.add(CompilerError(err))
        return None
    else:
        return path


def find_library(file):
    """Search the given library file by searching in common directories.

    If found, returns the path. Otherwise, returns None.
    """
    search_paths = [pathlib.Path("/usr/local/lib/x86_64-linux-gnu"),
                    pathlib.Path("/lib/x86_64-linux-gnu"),
                    pathlib.Path("/usr/lib/x86_64-linux-gnu"),
                    pathlib.Path("/usr/local/lib64"),
                    pathlib.Path("/lib64"),
                    pathlib.Path("/usr/lib64"),
                    pathlib.Path("/usr/local/lib"),
                    pathlib.Path("/lib"),
                    pathlib.Path("/usr/lib"),
                    pathlib.Path("/usr/x86_64-linux-gnu/lib64"),
                    pathlib.Path("/usr/x86_64-linux-gnu/lib")]

    for path in search_paths:
        full = path.joinpath(file)
        if full.is_file():
            return str(full)
    return None


if __name__ == "__main__":
    sys.exit(main())
