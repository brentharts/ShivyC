#ifndef _SHIVYC_SETJMP_H
#define _SHIVYC_SETJMP_H

/* Minimal setjmp.h for ShivyC's bundled fallback headers.
 *
 * The runtime (shivyc_rt.h) uses setjmp/longjmp for the transpiler's
 * exception mechanism, so any C that ShivyC compiles from its own transpiled
 * output must resolve <setjmp.h>. We only need a storage type of the right
 * size plus the two prototypes; setjmp/longjmp themselves resolve to glibc at
 * link time, so jmp_buf must match glibc's ABI footprint.
 *
 * On x86-64 glibc, jmp_buf is `struct __jmp_buf_tag[1]`, whose size is 200
 * bytes (8*8 saved registers + an int + a 128-byte sigset_t, padded). A flat
 * array of 25 longs (25 * 8 = 200) is layout-compatible for allocation, which
 * is all the caller does with it before passing &env[0] to setjmp/longjmp.
 */
typedef long jmp_buf[25];

int setjmp(jmp_buf env);
void longjmp(jmp_buf env, int val);

#endif /* _SHIVYC_SETJMP_H */
