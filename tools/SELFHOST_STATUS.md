ShivyCX self-hosting (transpile -> gcc) -- progress report
==========================================================

Transpiler: ShivyC/tools/py2c.py.  All modules TRANSPILE to C:
  top-level 28/28, tree 16/16, parser 6/6, il_cmds 7/7  (57/57).

The remaining work is making the generated C COMPILE under gcc.

Fixes landed this round (both general, both regression-checked)
---------------------------------------------------------------
1. Cross-module singleton recognition (is_obj_word).
   `error_collector` (imported from shivyc.errors) was treated as a tagged
   obj, so `error_collector.add(...)` wrapped it in AS_OBJ() and the C broke.
   Now imported singletons/funcs/classes are recognized as typed symbols.
   -> fixes the "'error_collector' is a pointer" gap in lexer.c and preproc.c
      (both now compile past it).

2. copy.copy(x) -> shallow struct copy.
   Was emitted as an unsupported dynamic call (OBJ_NONE) in standalone mode.
   Now a class instance is copied with `({ T* _cp = aalloc(sizeof(T));
   *_cp = *(x); _cp; })`, typed as x's type, in both the bare `copy(x)`
   (from-import) and `copy.copy(x)` forms, in emission AND value_ctype.
   -> il_gen.c: 8 of its 12 errors gone (the Context copies).

Compile coverage now: 18/57 modules
  top-level  9/28   tree 6/16   parser 1/6   il_cmds 2/7

Remaining gap categories (each a distinct, independent fix)
-----------------------------------------------------------
* Dynamic Python features needing ShivyCX source changes (or big transpiler
  work): collections.namedtuple (il_gen `Tables`), builtins.setattr,
  copy beyond shallow. These don't map cleanly to standalone C.
* Untyped-receiver method mis-dispatch: e.g. `il_code.add(x)` -> builtin
  set_add because il_code's cross-module type isn't inferred.
* Missing forward decls / externs for cross-module functions -> implicit-int
  -> arg-type errors (default_il_cmd, group, _contracts_current_function...).
* Runtime-helper arg coercion: subscript_set / dict_get / list_append /
  set_add / pystr / strcmp args passed unwrapped.
* "used struct value where scalar required", "invalid initializer",
  "returning int but obj expected", "cannot convert to a pointer type".

Highest-leverage next targets (unblock many modules each)
---------------------------------------------------------
* Forward-declare every module-level/cross-module function (kills the whole
  implicit-int -> arg-type-error family).
* Infer cross-module instance types so `.add/.append/...` dispatch to the
  class method instead of a set/list builtin.
