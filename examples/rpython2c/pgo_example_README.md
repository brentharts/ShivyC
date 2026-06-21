# pgo — profile-guided auto-typing (`-fprofile-generate`)

The name mirrors gcc's flag, but instead of timers we inject **type probes**.
The pipeline (in `tools/rpy_pgo.py`):

1. **instrument** — parse the script and, after every assignment / container
   mutation, inject a probe that records the runtime element/key/value types
   into a global dict. Every `for`/`while` loop is bounded to a small iteration
   budget so even a long-running script profiles in a moment.
2. **profile** — run the instrumented script in a subprocess; an `atexit` hook
   dumps the observed types to a JSON file in `/tmp`.
3. **rewrite** — read the types back and rewrite the *original* source,
   annotating each cleanly-typed, non-escaping empty `list`/`dict` as
   `name: "list[int]"` / `name: "dict[str,int]"`. py2c's existing typed-container
   path then lowers them to the unboxed form.

It is **best-effort and safe**: on any failure (script error, timeout, no
observations) the original source is compiled unchanged, and the same
escape/usage analysis used by static promotion gates every annotation — so PGO
auto-typing never changes observable behavior (`make testpgo` checks
boxed == profile-guided for a suite of programs).

## Why profiling beats static inference here

In `app.py`, `vals` and `cache` are filled from `square(i)` — a function call.
Static inference can't see through the call, so it leaves them boxed (`obj`).
Profiling observes the values are really `int`, so:

```
$ python3 tools/py2c.py app.py -fprofile-generate --out /tmp/d
  pgo: profiled app.py, auto-typed 2 container(s) -> ...
```

`vals` becomes `list[int]` and `cache` becomes `dict[int, int]`, both unboxed.

```
python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p; echo $?            # 70 (boxed)
RPY_PROFILE_GENERATE=1 python3 -m shivyc.main --no-cache app.py -o /tmp/p && /tmp/p   # 70 (unboxed)
```

The env var `RPY_PROFILE_GENERATE=1` is equivalent to the flag and also works
through the ShivyCX front end. `RPY_PROFILE_LOOP_BUDGET` (default 8) tunes how
many iterations of each loop the profiling run executes.

## Merging static + profiled evidence

For a container the profiling run actually populated, the observed types win (and
*veto*: a container that looks clean statically but was mixed at runtime stays
boxed). For a container on a code path the run never reached, py2c falls back to
static inference — so cold paths still get typed when they provably can.

## Caching the profile (`-fprofile-use`)

`-fprofile-generate[=PATH]` writes the observed types to a JSON file (default
`/tmp/rpy_profile.json`); `-fprofile-use[=PATH]` replays that profile and
recompiles **without** re-running the script.
