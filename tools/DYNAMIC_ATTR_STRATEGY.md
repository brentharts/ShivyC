Dynamic features: fastest vs. easily-doable (per the micropython idea)
=====================================================================

This round (pure C, fastest): untyped attribute *writes* now resolve through
the unique field owner, mirroring what attribute *reads* already did. So
`il_value.literal = X` (where `literal` is a declared ILValue field but
`il_value` is an unannotated param) lowers to
`((ILValue*)AS_OBJ(il_value))->literal = X` instead of a dynamic setattr.
  -> dynamic setattr calls: 45 -> 35; il_value.literal/node.r/var.ctype/...
     are now pure-C field stores. No regressions (28/28 transpile; the 9
     compiling top-level modules still compile).

Full-compile coverage is unchanged (18/57) because the affected modules still
have *other* independent blockers; this is incremental correctness that
compounds with later fixes rather than a coverage jump.

The genuinely-dynamic remainder (35 setattr) -- monkey-patched attributes that
are declared as fields in ZERO classes:
  arguments._wp_graph / ._thread_alloc / ._simd_pack_layout / ._inline_bodies
  _contracts.current_function ; p.cur_func_name / .tokens / .best_error
  node.r ; t.r / .logical_line ; member_elim.enabled ; il_code.stackless_info

Two ways to support these, by the fastest-vs-easiest tradeoff you raised:

  A. FASTEST (pure C) -- declare them as real fields.
     For ShivyCX-owned objects (p, t, node, decl, member_elim, il_code, the
     `_contracts` singleton) just add `self.<attr> = None` in __init__; they
     become struct offsets, zero dynamic cost. This honors the transpiler's
     core "fixed per-class attribute set" assumption. This is a ShivyCX
     *source* change, small and mechanical.

  B. EASILY-DOABLE (micropython object model) -- bridge setattr/getattr.
     For the external argparse `arguments` namespace (where adding a field is
     awkward), route the stash through micropython's dynamic-attribute object
     model (mp_setattr/mp_getattr already exist in the bridge). Costs a dynamic
     dispatch + an objcore dependency, so reserve it for the few external-object
     cases rather than the hot path.

Recommendation: do (A) for the ShivyCX-class attributes (fast, removes most of
the 35), and (B) only for `arguments.*` (4 attrs) -- or, even faster, give the
args object a tiny ShivyCX-owned wrapper with those 4 fields.
