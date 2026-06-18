"""An N-body gravity simulation in rpython -- several POD class instances.

Each `Body` is a plain data class (position, velocity, mass) lowered by ShivyCX
to a bare C struct (no object header / vtable / runtime):

    typedef struct Body { double x, y, vx, vy, mass; } Body;

The per-body updates are methods (scalar params -> direct calls); the pairwise
gravitational acceleration is computed by the free functions `gx`/`gy` reading
struct fields directly. Plain Euler integration with softening. The exit code is
a checksum of the final positions, so the trajectory is verifiable.

    python3 -m shivyc.main --no-cache nbody.py -o /tmp/nbody && /tmp/nbody
"""


def gx(px: "f64", py: "f64", qx: "f64", qy: "f64", qm: "f64") -> float:
    dx = qx - px
    dy = qy - py
    r2 = dx * dx + dy * dy + 0.05          # softening avoids the singularity
    return qm * dx / (r2 * sqrt(r2))


def gy(px: "f64", py: "f64", qx: "f64", qy: "f64", qm: "f64") -> float:
    dx = qx - px
    dy = qy - py
    r2 = dx * dx + dy * dy + 0.05
    return qm * dy / (r2 * sqrt(r2))


class Body:
    def __init__(self, x: "f64", y: "f64", vx: "f64", vy: "f64", mass: "f64"):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.mass = mass

    def kick(self, ax: "f64", ay: "f64", dt: "f64") -> None:
        self.vx = self.vx + ax * dt
        self.vy = self.vy + ay * dt

    def drift(self, dt: "f64") -> None:
        self.x = self.x + self.vx * dt
        self.y = self.y + self.vy * dt


def main() -> int:
    sun = Body(0.0, 0.0, 0.0, 0.0, 80.0)
    p1 = Body(2.0, 0.0, 0.0, 6.0, 1.0)
    p2 = Body(-3.0, 0.0, 0.0, -5.0, 1.0)
    dt = 0.002

    steps = 0
    while steps < 4000:
        sax = gx(sun.x, sun.y, p1.x, p1.y, p1.mass) + \
            gx(sun.x, sun.y, p2.x, p2.y, p2.mass)
        say = gy(sun.x, sun.y, p1.x, p1.y, p1.mass) + \
            gy(sun.x, sun.y, p2.x, p2.y, p2.mass)
        a1x = gx(p1.x, p1.y, sun.x, sun.y, sun.mass) + \
            gx(p1.x, p1.y, p2.x, p2.y, p2.mass)
        a1y = gy(p1.x, p1.y, sun.x, sun.y, sun.mass) + \
            gy(p1.x, p1.y, p2.x, p2.y, p2.mass)
        a2x = gx(p2.x, p2.y, sun.x, sun.y, sun.mass) + \
            gx(p2.x, p2.y, p1.x, p1.y, p1.mass)
        a2y = gy(p2.x, p2.y, sun.x, sun.y, sun.mass) + \
            gy(p2.x, p2.y, p1.x, p1.y, p1.mass)
        sun.kick(sax, say, dt)
        p1.kick(a1x, a1y, dt)
        p2.kick(a2x, a2y, dt)
        sun.drift(dt)
        p1.drift(dt)
        p2.drift(dt)
        steps = steps + 1

    return int((p1.x + p1.y + p2.x + p2.y) * 10.0) % 256
