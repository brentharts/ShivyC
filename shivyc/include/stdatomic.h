/* <stdatomic.h> -- C11 atomics, implemented over the __atomic_* builtins that
 * ShivyCX supports. This is a fallback header used when no real libc provides
 * one (e.g. when compiling against the packaged musl). It is sufficient for
 * single-threaded freestanding builds and for consumers such as mimalloc and
 * CPython that expect the C11 atomic API.
 */
#ifndef _STDATOMIC_H
#define _STDATOMIC_H

#include <stddef.h>
#include <stdint.h>

/* Memory order constants: values match the compiler's __ATOMIC_* builtins. */
typedef enum memory_order {
    memory_order_relaxed = 0,
    memory_order_consume = 1,
    memory_order_acquire = 2,
    memory_order_release = 3,
    memory_order_acq_rel = 4,
    memory_order_seq_cst = 5
} memory_order;

/* Lock-free property macros (assume always lock-free for supported widths). */
#define ATOMIC_BOOL_LOCK_FREE     2
#define ATOMIC_CHAR_LOCK_FREE     2
#define ATOMIC_CHAR16_T_LOCK_FREE 2
#define ATOMIC_CHAR32_T_LOCK_FREE 2
#define ATOMIC_WCHAR_T_LOCK_FREE  2
#define ATOMIC_SHORT_LOCK_FREE    2
#define ATOMIC_INT_LOCK_FREE      2
#define ATOMIC_LONG_LOCK_FREE     2
#define ATOMIC_LLONG_LOCK_FREE    2
#define ATOMIC_POINTER_LOCK_FREE  2

/* Initialization. ATOMIC_VAR_INIT is deprecated in C17 but still referenced. */
#define ATOMIC_VAR_INIT(value) (value)
#define atomic_init(obj, value) \
    __atomic_store_n((obj), (value), memory_order_relaxed)

/* kill_dependency: ShivyCX does not model consume dependencies. */
#define kill_dependency(y) (y)

/* Fences. */
#define atomic_thread_fence(order) __atomic_thread_fence(order)
#define atomic_signal_fence(order) __atomic_signal_fence(order)

#define atomic_is_lock_free(obj) (sizeof(*(obj)) <= sizeof(void *))

/* Atomic integer typedefs. The _Atomic qualifier is a no-op for codegen in a
 * single-threaded build but keeps the types distinct for the API. */
typedef _Atomic _Bool              atomic_bool;
typedef _Atomic char               atomic_char;
typedef _Atomic signed char        atomic_schar;
typedef _Atomic unsigned char      atomic_uchar;
typedef _Atomic short              atomic_short;
typedef _Atomic unsigned short     atomic_ushort;
typedef _Atomic int                atomic_int;
typedef _Atomic unsigned int       atomic_uint;
typedef _Atomic long               atomic_long;
typedef _Atomic unsigned long      atomic_ulong;
typedef _Atomic long long          atomic_llong;
typedef _Atomic unsigned long long atomic_ullong;
typedef _Atomic size_t             atomic_size_t;
typedef _Atomic ptrdiff_t          atomic_ptrdiff_t;
typedef _Atomic intptr_t           atomic_intptr_t;
typedef _Atomic uintptr_t          atomic_uintptr_t;
typedef _Atomic intmax_t           atomic_intmax_t;
typedef _Atomic uintmax_t          atomic_uintmax_t;

/* Generic operations (explicit memory order). */
#define atomic_store_explicit(obj, desired, order) \
    __atomic_store_n((obj), (desired), (order))
#define atomic_load_explicit(obj, order) \
    __atomic_load_n((obj), (order))
#define atomic_exchange_explicit(obj, desired, order) \
    __atomic_exchange_n((obj), (desired), (order))
#define atomic_compare_exchange_strong_explicit(obj, expected, desired, succ, fail) \
    __atomic_compare_exchange_n((obj), (expected), (desired), 0, (succ), (fail))
#define atomic_compare_exchange_weak_explicit(obj, expected, desired, succ, fail) \
    __atomic_compare_exchange_n((obj), (expected), (desired), 1, (succ), (fail))
#define atomic_fetch_add_explicit(obj, arg, order) \
    __atomic_fetch_add((obj), (arg), (order))
#define atomic_fetch_sub_explicit(obj, arg, order) \
    __atomic_fetch_sub((obj), (arg), (order))
#define atomic_fetch_or_explicit(obj, arg, order) \
    __atomic_fetch_or((obj), (arg), (order))
#define atomic_fetch_xor_explicit(obj, arg, order) \
    __atomic_fetch_xor((obj), (arg), (order))
#define atomic_fetch_and_explicit(obj, arg, order) \
    __atomic_fetch_and((obj), (arg), (order))

/* Generic operations (sequentially consistent). */
#define atomic_store(obj, desired) \
    atomic_store_explicit((obj), (desired), memory_order_seq_cst)
#define atomic_load(obj) \
    atomic_load_explicit((obj), memory_order_seq_cst)
#define atomic_exchange(obj, desired) \
    atomic_exchange_explicit((obj), (desired), memory_order_seq_cst)
#define atomic_compare_exchange_strong(obj, expected, desired) \
    atomic_compare_exchange_strong_explicit( \
        (obj), (expected), (desired), memory_order_seq_cst, memory_order_seq_cst)
#define atomic_compare_exchange_weak(obj, expected, desired) \
    atomic_compare_exchange_weak_explicit( \
        (obj), (expected), (desired), memory_order_seq_cst, memory_order_seq_cst)
#define atomic_fetch_add(obj, arg) \
    atomic_fetch_add_explicit((obj), (arg), memory_order_seq_cst)
#define atomic_fetch_sub(obj, arg) \
    atomic_fetch_sub_explicit((obj), (arg), memory_order_seq_cst)
#define atomic_fetch_or(obj, arg) \
    atomic_fetch_or_explicit((obj), (arg), memory_order_seq_cst)
#define atomic_fetch_xor(obj, arg) \
    atomic_fetch_xor_explicit((obj), (arg), memory_order_seq_cst)
#define atomic_fetch_and(obj, arg) \
    atomic_fetch_and_explicit((obj), (arg), memory_order_seq_cst)

/* atomic_flag. */
typedef struct atomic_flag { _Atomic _Bool _Value; } atomic_flag;
#define ATOMIC_FLAG_INIT { 0 }
#define atomic_flag_test_and_set_explicit(obj, order) \
    __atomic_test_and_set(&(obj)->_Value, (order))
#define atomic_flag_test_and_set(obj) \
    atomic_flag_test_and_set_explicit((obj), memory_order_seq_cst)
#define atomic_flag_clear_explicit(obj, order) \
    __atomic_clear(&(obj)->_Value, (order))
#define atomic_flag_clear(obj) \
    atomic_flag_clear_explicit((obj), memory_order_seq_cst)

#endif /* _STDATOMIC_H */
