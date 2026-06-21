# Run ShivyC's test suite with PyPy3 (much faster than CPython).
#
# The tests shell out to a `shivyc` executable (subprocess.run(["shivyc", ...])).
# That executable is only on PATH if the package's console-script was installed,
# which it is not in a fresh checkout.
# Rather than require `pip install`, we drop a tiny `bin/shivyc` shim that runs
# the compiler as a module under pypy3, and put it on PATH (with the repo root
# on PYTHONPATH so it imports without being installed). The shim also means the
# compiler itself runs under PyPy3.

ROOT := $(shell pwd)
export PYTHONPATH := $(ROOT)$(if $(PYTHONPATH),:$(PYTHONPATH),)
export PATH := $(ROOT)/bin:$(PATH)

# ---------------------------------------------------------------------------
# Micropython compile-testing
#
# `make install_micropython` clones (or updates) the objcore micropython fork
# and generates its qstr/module headers. The `test_micropython*` targets then
# compile-check slices of it through ShivyCX (under PyPy3) via tools/mpy_test.py.
MPY_REPO ?= https://github.com/OpenSourceJesus/micropython
MPY_DIR  ?= $(ROOT)/micropython
MPY_PORT := $(MPY_DIR)/ports/objcore
MPY_GENHDR := $(MPY_PORT)/build/genhdr/qstrdefs.generated.h

# Large external codebases compiled via the generic runner tools/bigtest.py.
# These targets are NOT part of `make test`/`make default`; they are opt-in and
# slow. bigtest skips large .c files by default (override via MAX_KB) and
# forwards extra defines/includes (DEFS / INCS) so quick experiments are easy:
#     make test_cpython DEFS='-D FOO=1' MAX_KB=128
MAX_KB ?= 64
DEFS   ?=
INCS   ?=
# Parallel compile jobs for the big-codebase targets; 0 = one job per CPU.
JOBS   ?= 0

# CPython object model, compiled against the built-in musl (--musl).
CPY_REPO ?= https://github.com/OpenSourceJesus/cpython-tinier
CPY_DIR  ?= $(ROOT)/cpython-tinier
CPY_CONFIG := $(CPY_DIR)/pyconfig.h
CPY_DEFS := -D Py_BUILD_CORE -D thread_local=_Thread_local \
            -D _Py_USE_GCC_BUILTIN_ATOMICS=1

# 2.11BSD userland, compiled with BSD's own headers.
BSD_REPO ?= https://github.com/brentharts/2.11BSD-riscv
BSD_DIR  ?= $(ROOT)/2.11BSD-riscv

default: shim
	cd tests && pypy3 ./test_all.py
	cd tests && pypy3 ./test_float.py

# Run the ENTIRE test suite via unittest discovery (still under pypy3).
test: shim
	cd tests && pypy3 -m unittest discover -s .

# Create bin/shivyc -> `pypy3 -m shivyc.main "$@"`.
shim:
	@mkdir -p bin
	@printf '#!/bin/sh\nexec pypy3 -m shivyc.main "$$@"\n' > bin/shivyc
	@chmod +x bin/shivyc

install:
	sudo apt-get update
	sudo apt-get install -y build-essential gcc gcc-multilib binutils make \
		python3 qemu-system-x86 git pypy3

clean:
	rm -rf bin build

# ---------------------------------------------------------------------------
# Self-hosting: transpile ShivyCX's own source to C and build/test/bench it.
#
# tools/selfhost.py transpiles ShivyCX modules with tools/py2c.py, compiles
# them, and links runnable test/benchmark exes. Two link backends: `host`
# (plain gcc, for pure-C modules like tokens) and `objcore` (links the
# micropython objcore core, for code that uses the dynamic mp bridge). All C
# test/bench harnesses are inlined in the script and written to /tmp at build
# time. These targets are NOT part of `make test`; they are opt-in.
#     make selfhost                 # end-to-end module tests (host backend)
#     make selfhost_objcore         # also the objcore-bridge test (needs the
#                                   #   objcore build: see install_micropython
#                                   #   + a built ports/objcore/build/py)
#     make selfhost_bench           # transpiled-code microbenchmarks
#     make selfhost_coverage        # how many modules gcc-compile (glibc)
#     make selfhost_coverage_musl   # ... against the packaged musl headers
selfhost:
	python3 tools/selfhost.py test

selfhost_objcore:
	python3 tools/selfhost.py test --objcore

selfhost_bench:
	python3 tools/selfhost.py bench

selfhost_coverage:
	python3 tools/selfhost.py coverage

selfhost_link:
	python3 tools/selfhost.py link

selfhost_coverage_musl:
	python3 tools/selfhost.py coverage --musl

# ---------------------------------------------------------------------------
# rpython examples
#
# Compile each rpython example straight to a native binary through
# shivyc.main's `.py` path (transpile -> C -> ShivyCX -> link) and check its
# exit code. Examples without a `main()` are libraries and are not listed here.
#     make rpython
RPY    := examples/rpython2c
RPYBIN := build/rpython
rpython:
	@mkdir -p $(RPYBIN)
	@fail=0; \
	run() { \
	  src=$$1; exp=$$2; in=$$3; out=$(RPYBIN)/`basename $$src .py`; \
	  if ! python3 -m shivyc.main --no-cache $$src -o $$out >/dev/null 2>&1; then \
	    echo "  FAIL(compile) $$src"; fail=1; return; fi; \
	  if [ -n "$$in" ]; then echo "$$in" | timeout 20 $$out >/dev/null 2>&1; \
	  else timeout 20 $$out >/dev/null 2>&1; fi; rc=$$?; \
	  if [ "$$rc" = "$$exp" ]; then echo "  ok    $$src (exit $$rc)"; \
	  else echo "  FAIL  $$src (exit $$rc, expected $$exp)"; fail=1; fi; }; \
	runm() { exp=$$1; shift; out=$(RPYBIN)/multi_`basename $$1 .py`; \
	  if ! python3 -m shivyc.main --no-cache "$$@" -o $$out >/dev/null 2>&1; then \
	    echo "  FAIL(compile) $$*"; fail=1; return; fi; \
	  timeout 20 $$out >/dev/null 2>&1; rc=$$?; \
	  if [ "$$rc" = "$$exp" ]; then echo "  ok    [multi] $$* (exit $$rc)"; \
	  else echo "  FAIL  [multi] $$* (exit $$rc, expected $$exp)"; fail=1; fi; }; \
	run $(RPY)/numpy/simd_kernels.py 55 ""; \
	run $(RPY)/numpy/simd_blas.py   186 ""; \
	run $(RPY)/numpy/ufuncs.py       49 ""; \
	run $(RPY)/numpy/matmul.py      239 ""; \
	run $(RPY)/nn/neural_net.py     199 ""; \
	run $(RPY)/nbody/nbody.py        11 ""; \
	run $(RPY)/classes/polymorphism.py 22 ""; \
	run $(RPY)/classes/pod_vs_object.py  48 ""; \
	run $(RPY)/lists/typed_list.py       65 ""; \
	run $(RPY)/dicts/typed_dict.py       58 ""; \
	run $(RPY)/compiler/lexer_kernel.py  13 ""; \
	run $(RPY)/memory/del_demo.py    60 ""; \
	run $(RPY)/memory/autofree.py   135 ""; \
	run $(RPY)/io/simple_io.py        5 world; \
	run $(RPY)/net/socket_echo.py     5 ""; \
	run $(RPY)/mandelbrot/mandelbrot.py 70 ""; \
	run $(RPY)/sysinfo/sysinfo.py        7 ""; \
	run $(RPY)/collections/containers.py 10 ""; \
	run $(RPY)/dynattr/app.py 126 ""; \
	run $(RPY)/rtattr/app.py 48 ""; \
	run $(RPY)/crossattr/app.py 114 ""; \
	run $(RPY)/aggregates/app.py 84 ""; \
	run $(RPY)/formatting/app.py 33 ""; \
	run $(RPY)/ctorval/app.py 23 ""; \
	run $(RPY)/sets/app.py 35 ""; \
	run $(RPY)/dictops/app.py 186 ""; \
	run $(RPY)/wordfreq/app.py 93 ""; \
	run $(RPY)/untyped/app.py 41 ""; \
	run $(RPY)/promote/app.py 70 ""; \
	runm 38 $(RPY)/multifile/app.py $(RPY)/multifile/geom.py; \
	runm 45 $(RPY)/ambig/app.py $(RPY)/ambig/node_a.py $(RPY)/ambig/node_b.py; \
	runm 55 $(RPY)/fieldwrite/app.py $(RPY)/fieldwrite/lib.py; \
	if [ $$fail = 0 ]; then echo "rpython examples: all passed"; \
	else echo "rpython examples: FAILURES"; fi; exit $$fail

# ---------------------------------------------------------------------------
# Fast smoke test: two oracle programs (a single-file syntax sweep and a
# multi-file cross-module case) compiled three ways -- CPython (the oracle),
# the ShivyCX self-compiler, and the py2c->gcc transpiler -- requiring all
# three to agree. Covers most of the language subset in a few seconds, so it
# stands in for the full suite when iterating.
#     make testfast
FAST    := tests/fast
FASTBIN := build/fast
testfast:
	@mkdir -p $(FASTBIN)
	@fail=0; \
	sx_run() { out=$(FASTBIN)/$$1_sx; shift; \
	  if ! python3 -m shivyc.main --no-cache "$$@" -o $$out >/dev/null 2>&1; then echo ERR; return; fi; \
	  timeout 30 $$out >/dev/null 2>&1; echo $$?; }; \
	gcc_run() { d=$(FASTBIN)/$$1_c; rm -rf $$d; mkdir -p $$d; shift; \
	  if ! python3 tools/py2c.py "$$@" --out $$d >/dev/null 2>&1; then echo ERR; return; fi; \
	  python3 -c "import sys;sys.path.insert(0,'tools');import py2c;py2c.write_runtime('$$d')" >/dev/null 2>&1; \
	  if ! gcc -std=c99 -I$$d $$d/*.c -o $$d/bin 2>/dev/null; then echo ERR; return; fi; \
	  timeout 30 $$d/bin >/dev/null 2>&1; echo $$?; }; \
	report() { if [ "$$3" = "$$2" ] && [ "$$4" = "$$2" ]; then \
	    echo "  ok    $$1 (cpython=$$2 shivycx=$$3 gcc=$$4)"; \
	  else echo "  FAIL  $$1 (cpython=$$2 shivycx=$$3 gcc=$$4)"; fail=1; fi; }; \
	orc=`python3 $(FAST)/syntax_core.py >/dev/null 2>&1; echo $$?`; \
	sx=`sx_run syntax_core $(FAST)/syntax_core.py`; \
	cc=`gcc_run syntax_core $(FAST)/syntax_core.py`; \
	report "syntax_core (single file)" $$orc $$sx $$cc; \
	orc=`cd $(FAST)/multi && python3 main.py >/dev/null 2>&1; echo $$?`; \
	M="$(FAST)/multi/main.py $(FAST)/multi/geometry.py $(FAST)/multi/shapes.py"; \
	sx=`sx_run multi $$M`; \
	cc=`gcc_run multi $$M`; \
	report "multi (cross-module)" $$orc $$sx $$cc; \
	if [ $$fail = 0 ]; then echo "testfast: PASS"; \
	else echo "testfast: FAIL"; fi; exit $$fail

# ---------------------------------------------------------------------------
# Promotion behavior-preservation check: compile a set of container-heavy
# programs with PY2C_PROMOTE_CONTAINERS=1 (auto-promote inferred containers to
# the unboxed typed form) and require the result to still match CPython. This
# guards that promotion never changes observable behavior.
#     make testpromote
testpromote:
	@mkdir -p $(FASTBIN)
	@fail=0; \
	chk() { src=$$1; cpy=`python3 $$src >/dev/null 2>&1; echo $$?`; \
	  d=$(FASTBIN)/promo_`basename $$src .py`; rm -rf $$d; mkdir -p $$d; \
	  if ! PY2C_PROMOTE_CONTAINERS=1 python3 tools/py2c.py $$src --out $$d >/dev/null 2>&1; then \
	    echo "  FAIL(transpile) $$src"; fail=1; return; fi; \
	  python3 -c "import sys;sys.path.insert(0,'tools');import py2c;py2c.write_runtime('$$d')" >/dev/null 2>&1; \
	  if ! gcc -std=c99 -I$$d $$d/*.c -o $$d/bin 2>/dev/null; then \
	    echo "  FAIL(gcc) $$src"; fail=1; return; fi; \
	  got=`timeout 30 $$d/bin >/dev/null 2>&1; echo $$?`; \
	  n=`PY2C_PROMOTE_CONTAINERS=1 python3 tools/py2c.py $$src --out $$d 2>&1 >/dev/null | grep -c promoted`; \
	  if [ "$$got" = "$$cpy" ]; then echo "  ok    $$src (==$$cpy, $$n promoted)"; \
	  else echo "  FAIL  $$src (promoted=$$got, cpython=$$cpy)"; fail=1; fi; }; \
	chk $(FAST)/syntax_core.py; \
	chk $(RPY)/promote/app.py; \
	chk $(RPY)/dictops/app.py; \
	chk $(RPY)/wordfreq/app.py; \
	chk $(RPY)/untyped/app.py; \
	if [ $$fail = 0 ]; then echo "testpromote: PASS"; \
	else echo "testpromote: FAIL"; fi; exit $$fail

# ---------------------------------------------------------------------------
# Benchmarks: whole-program SIMD vs gcc -O0/-O2, memory-safety table, etc.
#     make benchmarks
benchmarks:
	cd benchmarks && python3 run_benchmarks.py

# ---------------------------------------------------------------------------
# Micropython targets

# Clone the objcore micropython fork (or fast-forward an existing checkout),
# then generate the headers its sources need.
install_micropython:
	@if [ -d "$(MPY_DIR)/.git" ]; then \
		echo "Updating micropython in $(MPY_DIR)"; \
		git -C "$(MPY_DIR)" pull --ff-only; \
	else \
		echo "Cloning $(MPY_REPO) into $(MPY_DIR)"; \
		git clone "$(MPY_REPO)" "$(MPY_DIR)"; \
	fi
	$(MAKE) $(MPY_GENHDR)

# Generate micropython's qstr/module headers (required to preprocess its
# sources). micropython generates these before compiling any .c, so the headers
# appear even though the port's gcc build then trips the known hal.c
# warn_unused_result error -- which is why the gcc step's failure is ignored and
# success is verified by the header's presence instead.
$(MPY_GENHDR):
	-$(MAKE) -C $(MPY_PORT)
	@test -f $(MPY_GENHDR) || { \
		echo "ERROR: could not generate micropython headers."; \
		echo "Run 'make install_micropython' first."; exit 1; }

# Compile-check every part of micropython through ShivyCX (one warm PyPy3
# process). Reports a per-file summary; exits non-zero if any file fails.
test_micropython: $(MPY_GENHDR)
	pypy3 tools/mpy_test.py all --mpy-dir $(MPY_DIR) --quiet

# Individual slices, each usable as a standalone regression gate.
test_micropython_core: $(MPY_GENHDR)
	pypy3 tools/mpy_test.py core --mpy-dir $(MPY_DIR) --quiet

test_micropython_objects: $(MPY_GENHDR)
	pypy3 tools/mpy_test.py objects --mpy-dir $(MPY_DIR) --quiet

test_micropython_modules: $(MPY_GENHDR)
	pypy3 tools/mpy_test.py modules --mpy-dir $(MPY_DIR) --quiet

test_micropython_emitters: $(MPY_GENHDR)
	pypy3 tools/mpy_test.py emitters --mpy-dir $(MPY_DIR) --quiet

test_micropython_port: $(MPY_GENHDR)
	pypy3 tools/mpy_test.py port --mpy-dir $(MPY_DIR) --quiet

# Remove the micropython checkout entirely.
clean_micropython:
	rm -rf $(MPY_DIR)

# ---------------------------------------------------------------------------
# CPython targets (minimal, single-threaded, compiled against built-in musl).
# Opt-in and slow; large files are skipped by default (see MAX_KB).

install_cpython:
	@if [ -d "$(CPY_DIR)/.git" ]; then \
		echo "Updating cpython in $(CPY_DIR)"; \
		git -C "$(CPY_DIR)" pull --ff-only; \
	else \
		echo "Cloning $(CPY_REPO) into $(CPY_DIR)"; \
		git clone --depth 1 "$(CPY_REPO)" "$(CPY_DIR)"; \
	fi
	$(MAKE) $(CPY_CONFIG)

# pyconfig.h via configure (minimal build). Threads stay on (modern CPython
# requires them); ShivyCX treats _Thread_local as a plain global, which is
# correct for a single-threaded compile-check.
$(CPY_CONFIG):
	@test -d $(CPY_DIR) || { echo "Run 'make install_cpython' first."; exit 1; }
	cd $(CPY_DIR) && ./configure --without-pymalloc --disable-test-modules >/dev/null
	@test -f $(CPY_CONFIG) || { \
		echo "ERROR: configure did not produce pyconfig.h."; \
		echo "Run 'make install_cpython' first."; exit 1; }

# Compile-check CPython's object model. test_cpython == test_cpython_objects.
test_cpython test_cpython_objects: $(CPY_CONFIG)
	pypy3 tools/bigtest.py $(CPY_DIR) 'Objects/*.c' --musl --quiet \
		--jobs $(JOBS) --max-kb $(MAX_KB) -I . -I Include -I Include/internal \
		$(CPY_DEFS) $(DEFS) $(INCS)

clean_cpython:
	rm -rf $(CPY_DIR)

# ---------------------------------------------------------------------------
# 2.11BSD targets (classic Unix userland, BSD's own headers). Opt-in and slow.

install_bsd:
	@if [ -d "$(BSD_DIR)/.git" ]; then \
		echo "Updating 2.11BSD in $(BSD_DIR)"; \
		git -C "$(BSD_DIR)" pull --ff-only; \
	else \
		echo "Cloning $(BSD_REPO) into $(BSD_DIR)"; \
		git clone --depth 1 "$(BSD_REPO)" "$(BSD_DIR)"; \
	fi
	@# Classic 2.11BSD expects <sys/...> to resolve to the sys/h tree.
	ln -sfn ../sys/h $(BSD_DIR)/include/sys
	@echo "2.11BSD ready in $(BSD_DIR)"

# Compile-check the BSD userland utilities.
test_bsd test_bsd_bin:
	@test -d $(BSD_DIR) || { echo "Run 'make install_bsd' first."; exit 1; }
	pypy3 tools/bigtest.py $(BSD_DIR) 'bin/**/*.c' --quiet \
		--jobs $(JOBS) --max-kb $(MAX_KB) -I include $(DEFS) $(INCS)

test_bsd_usrbin:
	@test -d $(BSD_DIR) || { echo "Run 'make install_bsd' first."; exit 1; }
	pypy3 tools/bigtest.py $(BSD_DIR) 'usr.bin/**/*.c' --quiet \
		--jobs $(JOBS) --max-kb $(MAX_KB) -I include $(DEFS) $(INCS)

clean_bsd:
	rm -rf $(BSD_DIR)

# ---------------------------------------------------------------------------
# Bare-metal demos
#
# Compile a freestanding ShivyCX app and link it against the inlined mini-OS
# (no libc, no CRT) via shivycx_baremetal.py. `--image` additionally wraps the
# kernel in a 64-bit Multiboot boot stub so it can boot under QEMU. Sources live
# in examples/baremetal/; outputs land in build/.
BUILD ?= build

# Build every bare-metal demo (no QEMU needed).
baremetal: baremetal-hello baremetal-kernel baremetal-irq
	@echo "bare-metal images in $(BUILD)/:"; ls -1 $(BUILD)/*.elf

# Freestanding app linked against the mini-OS console.
baremetal-hello: shim
	@mkdir -p $(BUILD)
	pypy3 shivycx_baremetal.py examples/baremetal/hello.c -o $(BUILD)/hello.elf

# Bootable 64-bit image that prints to VGA/serial.
baremetal-kernel: shim
	@mkdir -p $(BUILD)
	pypy3 shivycx_baremetal.py examples/baremetal/kernel.c -o $(BUILD)/kernel.elf --image

# Bootable image with timer + keyboard via the 64-bit IDT.
baremetal-irq: shim
	@mkdir -p $(BUILD)
	pypy3 shivycx_baremetal.py examples/baremetal/kernel_irq.c -o $(BUILD)/irq.elf --image

# gcc-build the inlined 32-bit MiniKraft baseline from minikraft.py.
minikraft: shim
	@mkdir -p $(BUILD)
	pypy3 minikraft.py --build $(BUILD)/minikraft

# Boot the timer+keyboard image under QEMU (serial to stdout).
run-irq: baremetal-irq
	qemu-system-x86_64 -kernel $(BUILD)/irq.elf -serial stdio

self:
	cd tools && pypy3 py2c.py

.PHONY: default test testfast testpromote shim install clean baremetal baremetal-hello \
        selfhost selfhost_objcore selfhost_bench selfhost_coverage \
        selfhost_coverage_musl selfhost_link \
        rpython benchmarks \
        baremetal-kernel baremetal-irq minikraft run-irq \
        install_micropython clean_micropython test_micropython \
        test_micropython_core test_micropython_objects \
        test_micropython_modules test_micropython_emitters test_micropython_port \
        install_cpython clean_cpython test_cpython test_cpython_objects \
        install_bsd clean_bsd test_bsd test_bsd_bin test_bsd_usrbin
