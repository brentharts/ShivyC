#include <stdio.h>
#include "py/builtin.h"
#include "py/gc.h"
#include "py/lexer.h"
#include "py/mperrno.h"
#include "py/runtime.h"
#include "py/mphal.h"
#include "shivyc_rt.h"
#include "mp_stdlib_bridge.h"

static char *stack_top;
static char heap[MICROPY_HEAP_SIZE];

int main(void) {
    int stack_dummy; stack_top = (char*)&stack_dummy;
    gc_init(heap, heap + sizeof(heap));
    mp_init();
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        obj r = mp_call_import("builtins", "abs", 1, OBJ_INT(-5));
        printf("abs(-5) via objcore bridge = %ld (expect 5)\n", AS_INT(r));
        nlr_pop();
    } else {
        printf("micropython raised during bridge call\n");
    }
    mp_deinit();
    return 0;
}
void gc_collect(void){ void*d; gc_collect_start(); gc_collect_root(&d,((mp_uint_t)stack_top-(mp_uint_t)&d)/sizeof(mp_uint_t)); gc_collect_end(); }
mp_lexer_t *mp_lexer_new_from_file(qstr f){(void)f; mp_raise_OSError(MP_ENOENT);}
mp_import_stat_t mp_import_stat(const char *p){(void)p; return MP_IMPORT_STAT_NO_EXIST;}
void nlr_jump_fail(void *v){(void)v; for(;;){}}
/* HAL output (normally in hal.c) */
void mp_hal_stdout_tx_strn_cooked(const char *str, size_t len){ fwrite(str,1,len,stdout); }
/* float disabled in this objcore config; bridge refs are dead code for abs */
mp_obj_t mp_obj_new_float(double v){(void)v; return mp_const_none;}
double mp_obj_get_float(mp_obj_t o){(void)o; return 0;}
const mp_obj_type_t mp_type_float;
