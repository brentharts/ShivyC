Declaring dynamic-attribute fields (fastest, pure C)
====================================================

Changes (all verified against the ShivyCX test suite: errors=29, zero
failures -- identical to pristine; the 18/57 compiling modules still compile):

SOURCE (shivyc/):
  * tokens.py   Token.__init__: self.logical_line = None
  * il_gen.py   ILCode.__init__: self.stackless_info = {}

  IMPORTANT lesson: pre-initialized defaults MUST match the value readers pass
  to getattr(obj, attr, <default>). logical_line is read with default None (ok
  as None). stackless_info is read with getattr(il_code,"stackless_info",{}) --
  initializing it to None instead of {} broke 167 tests (a fresh ILCode then
  returned None where readers expected {} and called .get on it). Fixed to {}.

TRANSPILER (tools/py2c.py):
  * Module-attribute writes now lower to module-global stores instead of
    dynamic setattr. The attribute *read* path already resolved
    `p.cur_func_name` -> the global `p_cur_func_name`; the *write* was still
    emitting setattr(p,...). Now `_attr_assign_needs_setattr` returns False for
    a module-alias base, so the write resolves identically:
      p.cur_func_name = v        -> p_cur_func_name = v
      _contracts.current_function = v / member_elim.enabled = v  likewise
    The module globals are already declared in source, so this stays pure C.

Dynamic setattr calls: 45 -> 25 over the last two rounds
  (this round: 35 -> 25; -1 from logical_line, -9 from module-attribute writes).

Full-compile coverage: still 18/57 -- the touched modules have other,
independent blockers, so this is incremental correctness that compounds rather
than a coverage jump.

Remaining 25 setattr -- and the right tool for each:
  * arguments._wp_graph / ._thread_alloc / ._simd_pack_layout / ._inline_bodies
    (8): stashed on the EXTERNAL argparse Namespace. No ShivyCX class owns them,
    so a field declaration can't help. -> micropython object-model bridge
    (mp_setattr/mp_getattr), OR a tiny ShivyCX-owned args wrapper with 4 fields
    (faster, pure C).
  * node.r / decl.r / t.r  and  info.identifier (~6): the field DOES exist but
    on MULTIPLE classes, so the unique-owner resolver declines (ambiguous).
    -> needs type inference of the receiver (node/decl/t/info), not a field decl.
  * the rest: assorted, mostly the same two causes.

Next-highest lever: receiver type inference for the ambiguous-field cases, and
a small wrapper (or bridge) for the external argparse stashes.
