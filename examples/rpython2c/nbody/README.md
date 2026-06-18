# rpython N-body simulation — many structs interacting

`nbody.py` integrates three gravitating bodies (a "sun" and two planets) with
Euler steps. It shows several instances of a POD class working together: `Body`
is a plain data class, so ShivyCX lowers it to a bare struct allocated with
`malloc`, with methods compiled to direct calls and no runtime:

```c
typedef struct Body { double x, y, vx, vy, mass; } Body;
Body* Body_new(double x, double y, double vx, double vy, double mass);
void  Body_kick(Body* self, double ax, double ay, double dt);   /* direct call */
void  Body_drift(Body* self, double dt);
```

Per-body updates are scalar-parameter methods; the pairwise gravitational
acceleration is computed by the free functions `gx`/`gy`, which read body fields
directly (`sqrt` lowers to native libm). The exit code is a checksum of the final
positions:

```
python3 -m shivyc.main --no-cache nbody.py -o /tmp/nbody && /tmp/nbody
echo $?      # 11
```

Note: class instances aren't passed *as* arguments here — a class-typed
parameter still uses ShivyCX's boxed-object ABI, so the simulation keeps inter-
body math in free functions that take plain doubles.
