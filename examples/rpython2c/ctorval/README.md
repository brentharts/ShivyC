# ctorval — constructors as values, with int/float/bool args

Using a class as a *value* (storing it in a list, passing it to a function) makes
the transpiler emit a closure trampoline, `Cls__ctortramp(env, args)`, that
unpacks a runtime argument list and calls the real `Cls_new(...)`. Every argument
must be unboxed to the constructor's declared C parameter type.

Two bugs lived on this path:

1. **`double`/`float` arguments were not unboxed.** The trampoline handled `int`
   (`AS_INT`), `bool` (`truthy`), `char*` and pointers, but passed a float
   argument straight through as a raw 16-byte `obj`, where a `double` was
   expected. It now uses **`as_dbl`** (widen an int/bool, read a float's payload).

2. **`switch` on a narrow type always took the first case.** `truthy` dispatches
   with `switch (v.tag)` where the tag is one byte. The backend did not apply C's
   integer promotion to the switch controlling expression (6.8.4.2), so a
   sub-`int` control compared incorrectly — making every boolean read as false.
   The fix promotes the control value to `int` before the case dispatch, so the
   trampoline's `bool` argument *and* ordinary boolean-in-a-list truthiness are
   correct.

```c
/* trampoline, after the fix */
Vec_new(AS_INT(arg0), as_dbl(arg1), truthy(arg2));
```

```c
/* SwitchStatement.make_il, after the fix */
if val.ctype.size < ctypes.integer.size:
    val = set_type(val, ctypes.integer, il_code)   /* integer promotion */
```

CPython, `gcc`, and ShivyCX-self-compiled all exit **23** (`5 + 5 + 10` from the
trampoline-built `Vec`, plus `3` truthy flags).

## Run

```
python3 examples/rpython2c/ctorval/app.py ; echo $?                       # 23
python3 -m shivyc.main --no-cache examples/rpython2c/ctorval/app.py -o /tmp/v
/tmp/v ; echo $?                                                          # 23
```
