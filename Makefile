default:
	cd tests && pypy3 ./test_all.py
	cd tests && pypy3 ./test_float.py

install:
	sudo apt-get update
	sudo apt-get install -y build-essential gcc gcc-multilib binutils make python3 qemu-system-x86 git pypy3
	

