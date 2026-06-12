/* A stray semicolon (empty member) inside a struct/union arises from macros
   like CPython's Py_ARRAY_LENGTH, which expands a _Static_assert to nothing.
   GCC and Clang accept empty members; ShivyCX skips them. Returns 0. */

struct S {
    ;               /* leading empty member */
    int a;
    ;               /* between members */
    int b;
    ;;              /* doubled */
};

union U { ; int x; ; float y; ; };

int main(void) {
    struct S s;
    s.a = 2;
    s.b = 5;
    if (s.a + s.b != 7) return 1;
    if (sizeof(struct { int dummy; ; }) == 0) return 2;   /* the Py_ARRAY_LENGTH shape */
    union U u;
    u.x = 9;
    if (u.x != 9) return 3;
    return 0;
}
