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
        baremetal-kernel baremetal-irq minikraft run-irq
