"""An N-body gravity simulation in rpython -- class instances passed by pointer.

`Body` is a plain data class, lowered by ShivyCX to a bare C struct:

    typedef struct Body { double x, y, vx, vy, mass; } Body;

Because POD classes pass by *pointer* (not the boxed object ABI), a function can
take class instances directly: `add_gravity(p: "Body*", q: "Body*", dt)` becomes
`void add_gravity(Body* p, Body* q, double dt)` and is called as
`add_gravity(sun, p1, dt)` -- no boxing, no runtime. The exit code is a checksum
of the final positions.

    python3 -m shivyc.main --no-cache nbody.py -o /tmp/nbody && /tmp/nbody
"""


class Body:
    def __init__(self, x: "f64", y: "f64", vx: "f64", vy: "f64", mass: "f64"):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.mass = mass

    def drift(self, dt: "f64") -> None:
        self.x = self.x + self.vx * dt
        self.y = self.y + self.vy * dt


def add_gravity(p: "Body*", q: "Body*", dt: "f64") -> None:
    """Accelerate body p toward body q (softened) -- both passed by pointer."""
    dx = q.x - p.x
    dy = q.y - p.y
    r2 = dx * dx + dy * dy + 0.05
    f = q.mass * dt / (r2 * sqrt(r2))
    p.vx = p.vx + f * dx
    p.vy = p.vy + f * dy


def main() -> int:
    sun = Body(0.0, 0.0, 0.0, 0.0, 80.0)
    p1 = Body(2.0, 0.0, 0.0, 6.0, 1.0)
    p2 = Body(-3.0, 0.0, 0.0, -5.0, 1.0)
    dt = 0.002

    steps = 0
    while steps < 4000:
        add_gravity(sun, p1, dt)
        add_gravity(sun, p2, dt)
        add_gravity(p1, sun, dt)
        add_gravity(p1, p2, dt)
        add_gravity(p2, sun, dt)
        add_gravity(p2, p1, dt)
        sun.drift(dt)
        p1.drift(dt)
        p2.drift(dt)
        steps = steps + 1

    return int((p1.x + p1.y + p2.x + p2.y) * 10.0) % 256
