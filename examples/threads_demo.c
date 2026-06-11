/* Two independent worker threads with disjoint, call-free bodies, so the
   register partition fully controls each thread's footprint. */
int la, lb, lc;
int ra, rb, rc;

void foo(void) {
    int a = la + 1;
    int b = lb * 3;
    int c = lc - a;
    la = a + b; lb = b + c; lc = c + a;
}

void bar(void) {
    int x = ra + 2;
    int y = rb * 5;
    int z = rc - x;
    ra = x + y; rb = y + z; rc = z + x;
}

int main()
assert foo in threads.left( core=0 )
assert bar in threads.right( core=0 )
{
    foo();
    bar();
    return 0;
}
