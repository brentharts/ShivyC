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

.PHONY: default test shim install clean baremetal baremetal-hello \
        baremetal-kernel baremetal-irq minikraft run-irq \
        install_micropython clean_micropython test_micropython \
        test_micropython_core test_micropython_objects \
        test_micropython_modules test_micropython_emitters test_micropython_port
