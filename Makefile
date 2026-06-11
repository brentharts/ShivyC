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
	rm -rf bin

.PHONY: default test shim install clean
