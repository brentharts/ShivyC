/* GCC inline-asm dialect alternatives {att|intel|...}. ShivyCX emits asm under
   .att_syntax, so the AT&T variant is selected; a lone '{' (as in CPython's
   _Py_get_machine_stack_pointer) is dropped. Returns 0 on success. */

static unsigned long get_sp(void) {
    unsigned long result;
    __asm__("{movq %%rsp, %0" : "=r" (result));
    return result;
}

int main(void) {
    return get_sp() != 0 ? 0 : 1;   /* the stack pointer is never null */
}
