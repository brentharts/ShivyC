objcore linking (validated) + receiver type inference
======================================================

1) LINKING WITH MICROPYTHON OBJCORE  -- works end to end
--------------------------------------------------------
A ShivyCX-transpiled bridge call now compiles against objcore's headers, links
with the micropython core objects, and runs against the live VM:

    abs(-5) via objcore bridge = 5

Proven steps (see link_objcore.sh + harness.c):
  * mp_stdlib_bridge.c (the MP_BRIDGE_C runtime, emitted by write_runtime(
    mp_bridge=True)) compiles cleanly against objcore headers
    (-I<port> -I<micropython-top> -I<port>/build).
  * It links with the prebuilt core objects build/py/*.o (132 of them),
    excluding the port's main.o/hal.o/script.o; a small harness supplies
    mp_init()/gc_init() and the port stubs (gc_collect, mp_lexer_new_from_file,
    mp_import_stat, nlr_jump_fail) plus mp_hal_stdout_tx_strn_cooked.
  * Float is disabled in this objcore config, so the bridge's float refs
    (mp_obj_new_float/mp_obj_get_float/mp_type_float) are satisfied with dead
    stubs; enabling MICROPY_PY_BUILTINS_FLOAT would replace those.
  * objcore itself builds except hal.c trips -Werror=unused-result on read();
    not needed for the link (we supply the HAL output fn ourselves).

This is the proof that the genuinely-dynamic features (e.g. the external
argparse `arguments._wp_graph` stashes) can be served by micropython's object
model via mp_setattr/mp_getattr -- useful later, as planned.

2) RECEIVER TYPE INFERENCE (for ambiguous fields)
-------------------------------------------------
`r` is a field of BOTH the Node base and Token (unrelated), so the unique-owner
resolver declines and `t.r = ...` fell back to setattr. Fix (pure C):
  * Transpiler: iter_elem_ctype now infers a loop variable's type from a called
    function annotated `-> list[T]`, and a local assigned from such a call
    (`toks = tokenize(...)`) carries that element type, so `for t in toks` gives
    `t` the type Token.
  * Source: annotated `tokenize(...) -> "list[Token]"`.
  -> `t.r = r` now lowers to `(t)->r = ...` (pure-C field store). setattr 25->24.
  No regressions (ShivyCX suite: errors=29, identical to pristine).

Still ambiguous: node.r / decl.r (in the @add_range decorator, `node` is the
return of an arbitrary parse_func -> genuinely any Node subclass). `r` lives on
the Node base, so this needs the receiver typed as "some Node", which a single
annotation can't express for a polymorphic decorator. Options: type parse_func
returns as a Node base, or handle add_range specially. Deferred -- low yield
(range-tracking sites, not module blockers) relative to the objcore work.

Coverage unchanged at 18/57 (these are correctness gains that compound; the
touched modules still have other independent blockers).
