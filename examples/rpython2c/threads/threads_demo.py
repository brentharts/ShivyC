## Two independent worker threads with disjoint, call-free bodies, so the
## register partition fully controls each thread's footprint.
##
## The `@rpy.threads.left/right(core=N)` decorators are no-ops under CPython
## (they just tag the function), and `rpy.threads.start_new_thread` spawns a real
## thread there. Translated by py2c, the decorators become the ShivyCX register
## -partition contract `assert FN in threads.SIDE(core=N)` in main's header, and
## each start_new_thread(fn) lowers to a direct fn() call.

import rpy

la = 0
lb = 0
lc = 0
ra = 0
rb = 0
rc = 0


@rpy.threads.left(core=0)
def foo() -> None:
    global la, lb, lc
    a = la + 1
    b = lb * 3
    c = lc - a
    la = a + b
    lb = b + c
    lc = c + a


@rpy.threads.right(core=0)
def bar() -> None:
    global ra, rb, rc
    x = ra + 2
    y = rb * 5
    z = rc - x
    ra = x + y
    rb = y + z
    rc = z + x


if __name__ == "__main__":
    rpy.threads.start_new_thread(foo)
    rpy.threads.start_new_thread(bar)
