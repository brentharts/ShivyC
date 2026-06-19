# sysinfo — `sys.argv` and `sys.implementation.name`

A minimal rpython example showing two pieces of `sys` support in the
translator (`tools/py2c.py`):

## `sys.implementation.name` is `'shivyc'`

The translator identifies itself as `shivyc`, so source can fence off
host-CPython-only code:

```python
if sys.implementation.name != 'shivyc':
    import platform                       # host-only module
    return "host-" + platform.python_implementation()
return "shivyc"
```

The guarded branch is folded away **at translation time** — it is never lowered
to C — so it may use anything the C runtime lacks (here, `platform`). The same
file still runs correctly under CPython, where the branch is taken normally.
This is the rpython idiom for keeping host-only scaffolding (pickle caches, os
calls, etc.) out of the translated compiler.

## `sys.argv` → `argv` / `argc`

`sys.argv[i]` lowers to `argv[i]` (a `char*`) and `len(sys.argv)` to `argc`;
`if sys.argv:` becomes `argc > 0`. Reading argv makes the emitted entry point
`int main(int argc, char** argv)`.

## Build & run

```
python3 -m shivyc.main --no-cache sysinfo.py -o sysinfo
./sysinfo            # prints the program name then "shivyc"; exits 7 (6 + argc)
./sysinfo a b        # exits 9 (6 + argc==3)
```

The exit code is `len("shivyc") + argc`, so a wrong `impl` branch or a broken
`argv`→`argc` lowering would change it.
