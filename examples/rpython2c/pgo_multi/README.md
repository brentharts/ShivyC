# pgo_multi — multi-file profile-guided auto-typing

A two-file program (`app.py` imports `from hist import histogram`) compiled as
one translation unit. `hist.histogram` keeps an untyped `counts` dict; nothing in
`hist.py` reveals its types statically. A single profiling run driven from the
entry (`app.py`) instruments **both** modules into one shared dir with a shared
probe, so the run observes `counts` across the module boundary and types it as
`dict[int, int]`.

```
# boxed (default):
python3 -m shivyc.main app.py hist.py -o app && ./app; echo $?              # 44
# profile-guided (one run instruments both files):
python3 tools/py2c.py app.py hist.py -fprofile-generate --out /tmp/d        # counts -> dict[int,int]
```

Multi-file profiling is driven through the `tools/py2c.py` CLI (which sees the
whole file set at once); the entry module is the first file with a `__main__`
guard. `make testpgo` checks boxed == profile-guided for this program too.
