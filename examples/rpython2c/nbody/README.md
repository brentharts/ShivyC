# rpython N-body simulation — passing class instances by pointer

`nbody.py` integrates three gravitating bodies (a "sun" and two planets) with
Euler steps. It shows a POD class **passed directly to functions and methods**.

`Body` is a plain data class, so ShivyCX lowers it to a bare struct, and since
POD classes pass by *pointer* (not the boxed object ABI), a function can take
class instances as arguments:

```c
typedef struct Body { double x, y, vx, vy, mass; } Body;

void add_gravity(Body* p, Body* q, double dt) { ... }   /* p, q are pointers   */
void Body_drift(Body* self, double dt) { ... }          /* method, direct call */
```

```python
def add_gravity(p: "Body*", q: "Body*", dt: "f64") -> None:
    dx = q.x - p.x
    ...
    p.vx = p.vx + f * dx

add_gravity(sun, p1, dt)     # call -> add_gravity(sun, p1, dt), no boxing
```

The whole program is runtime-free (`sqrt` lowers to native libm). The exit code
is a checksum of the final positions:

```
python3 -m shivyc.main --no-cache nbody.py -o /tmp/nbody && /tmp/nbody
echo $?      # 11
```

(Passing a class *by value* — `Body` rather than `Body*` — would still need the
SysV two-eightbyte struct rule in the code generator; passing by pointer needs
no such support, since a pointer is a single 8-byte register.)
