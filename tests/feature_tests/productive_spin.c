/* Micro-slicing demo: productive spinning (paper section 7.1).
 *
 * A core waiting on a contended lock normally burns its cycles polling. Here,
 * while waiting, it runs *slices* of an independent, pure computation instead,
 * so the otherwise-wasted spin window completes useful work -- without delaying
 * entry to the critical section by more than one slice.
 *
 *   python3 -m shivyc.main examples/microslice/productive_spin.c --microslice
 *   python3 -m shivyc.main examples/microslice/productive_spin.c -o spin && ./spin
 */
#include <stdio.h>

/* ---- the pure, independent fragment ----------------------------------------
 * frag_ref reads nothing shared, writes no caller memory, calls nothing impure:
 * a pure value-returning loop. The micro-slicing analysis identifies exactly
 * this shape as safe to interleave with a spin-wait. */
long frag_ref(int n) {
    long acc = 0;
    int i = 0;
    while (i < n) {
        acc += (long)i * (long)i;
        i += 1;
    }
    return acc;
}

/* ---- the fragmentor's output: a resumable stepper --------------------------
 * The same loop, made resumable. Its state (i, acc) is private to the spinning
 * thread and disjoint from any lock-protected memory, so advancing it a slice
 * at a time between polls is safe. Each call runs at most `k` iterations, so a
 * slice's cost is bounded -- which bounds the added acquisition latency. */
typedef struct { int i; int n; long acc; } frag_slice;
void frag_slice_init(frag_slice *s, int n) { s->i = 0; s->n = n; s->acc = 0; }
int  frag_slice_step(frag_slice *s, int k) {
    int budget = k;
    while (s->i < s->n && budget > 0) {
        s->acc += (long)s->i * (long)s->i;
        s->i += 1;
        budget -= 1;
    }
    return s->i < s->n;            /* 1 while work remains */
}

/* ---- a contended lock ------------------------------------------------------
 * `held` models the cycles the *other* core holds the critical section: each
 * poll ticks it down. On real hardware this is a test_and_set on a shared word
 * released by another core; the mechanism below is identical either way. */
int try_acquire(int *held) {
    if (*held > 0) { *held -= 1; return 0; }   /* still held */
    return 1;                                   /* acquired */
}

/* idle spin: poll until free, wasting every cycle of the window */
int acquire_idle(int *held) {
    int polls = 0;
    while (!try_acquire(held)) polls += 1;
    return polls;
}

/* productive spin: run one slice of the independent fragment per poll */
int acquire_productive(int *held, frag_slice *s, int slice) {
    int polls = 0;
    while (!try_acquire(held)) {
        polls += 1;
        frag_slice_step(s, slice);             /* ~one slice of useful work */
    }
    return polls;
}

int main(void) {
    int N = 20000;                 /* size of the independent fragment */
    int H = 4000;                  /* how long the other core holds the lock */
    int slice = 8;                 /* iterations per poll: the micro-slicing
                                      pass recommends 8 for a ~32 ns budget
                                      (--slice-budget 96), since frag_ref costs
                                      ~12 units/iteration. The lock is therefore
                                      re-checked every <= 8 iterations, bounding
                                      the added acquisition latency by one slice. */

    long ref = frag_ref(N);        /* correct result of the fragment */

    /* Scenario A: idle spinning. */
    int held_a = H;
    int polls_a = acquire_idle(&held_a);

    /* Scenario B: productive spinning, same held duration. */
    int held_b = H;
    frag_slice s;
    frag_slice_init(&s, N);
    int polls_b = acquire_productive(&held_b, &s, slice);
    int done_during_spin = s.i;            /* iterations finished for free */
    frag_slice_step(&s, N);                /* finish any remainder after acquire */
    int correct = (s.acc == ref);

    printf("idle spin       : %d polls, 0 useful iterations (all wasted)\n",
           polls_a);
    printf("productive spin : %d polls, %d useful iterations during the spin\n",
           polls_b, done_during_spin);
    printf("fragment result : %s (acc=%ld, ref=%ld)\n",
           correct ? "correct" : "WRONG", s.acc, ref);
    return correct ? 0 : 1;
}
