/* The address of a file-scope compound literal is an address constant (C11
   6.5.2.5p5), usable in a static initializer. CPython's module-definition
   tables rely on this, e.g. {Py_mod_..., (void*)&(PyABIInfo){...}}. ShivyCX
   now materializes such a literal as an anonymous static object. Returns 0. */

struct P { int a; int b; };
struct Slot { int id; void *val; };

static struct Slot slots[] = {
    {1, (void*) &(struct P){ 10, 20 }},
    {2, &(struct P){ 30, 40 }},
    {0, ((void*)0)},
};

/* a scalar static pointer to a compound literal, too */
static int *const ip = (int[]){ 7, 8, 9 };

int main(void) {
    struct P *p0 = (struct P*) slots[0].val;
    struct P *p1 = (struct P*) slots[1].val;
    if (slots[0].id != 1 || p0->a != 10 || p0->b != 20) return 1;
    if (slots[1].id != 2 || p1->a != 30 || p1->b != 40) return 2;
    if (slots[2].val != ((void*)0)) return 3;
    if (ip[0] != 7 || ip[2] != 9) return 4;
    return 0;
}
