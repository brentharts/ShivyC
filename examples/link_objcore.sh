#!/bin/bash
# Proof: link a ShivyCX-transpiled module (mp_bridge=True) against micropython
# objcore and call into the live VM. Run from a dir holding shivyc_rt.{c,h},
# mp_stdlib_bridge.{c,h}, harness.c (with mp_init + port stubs).
set -e
OC=/home/claude/micropython/ports/objcore   # objcore port (built: build/py/*.o)
TOP=/home/claude/micropython
INC="-I. -I$OC -I$TOP -I$OC/build -std=c99"
gcc -c harness.c          -o harness.o          $INC
gcc -c mp_stdlib_bridge.c -o mp_stdlib_bridge.o $INC
gcc -c shivyc_rt.c        -o shivyc_rt.o        -I. -std=c99
CORE=$(find $OC/build/py $OC/build/shared -name '*.o' | grep -vE 'build/(main|hal|script)\.o')
gcc -no-pie -o oclink harness.o shivyc_rt.o mp_stdlib_bridge.o $CORE
./oclink
