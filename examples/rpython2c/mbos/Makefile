# mbos -- minibrowser on bare-metal VGA.
#
# Default build is a 64-bit long-mode kernel that renders the DOM into a
# Bochs-VBE graphics framebuffer with an 8x16 font. It boots with a plain
# `qemu-system-x86_64 -kernel` (Multiboot1 AOUT kludge, no GRUB).
#
#   make            build build/mbos.elf (64-bit, graphics)
#   make run        boot in a QEMU window
#   make serial     boot headless, page text streamed to your terminal
#   make test       headless self-test: assert rendered text + save a screenshot
#   make run-net / test-net    fetch the page over virtio-net + ARP + UDP
#   make hires / test-hires    1920x1080 via -device VGA,vgamem_mb=64
#   make rpython[-test][-net]  the same, with dom/html/render generated from
#                              rpython by py2c (see rpy/README.md)
#   make text32     the original 32-bit text-mode kernel (build/mbos_text32.elf)
#   make clean

BUILD := build
ELF   := $(BUILD)/mbos.elf

CC := gcc
LD := ld

# 64-bit freestanding. -mno-red-zone: no red zone in kernel; SSE stays on and is
# enabled in boot64.S so -O2 may use it. Small code model (we live at 1 MiB).
CFLAGS  := -m64 -std=c11 -ffreestanding -fno-pic -mno-red-zone \
           -fno-stack-protector -fno-builtin -nostdlib -mcmodel=small \
           -Wall -Wextra -O2
LDFLAGS := -n -T linker64.ld -nostdlib

QEMU        := qemu-system-x86_64
QEMU_COMMON := -kernel $(ELF) -no-reboot -vga std

# Graphics geometry (fits std VGA's 16 MiB default). Override for hi-res.
GFX_W ?= 1024
GFX_H ?= 768
CFLAGS += -DMBOS_GFX_W=$(GFX_W) -DMBOS_GFX_H=$(GFX_H)

COBJS := main.o console.o libmini.o dom.o html.o render.o net.o vbe.o

.PHONY: all run serial test run-net test-net hires test-hires \
        rpython rpython-test rpython-test-net text32 clean

all: $(ELF)

$(BUILD):
	@mkdir -p $(BUILD)

page_html.h: page.html gen_page.py
	python3 gen_page.py page.html page_html.h

$(BUILD)/boot64.o: boot64.S | $(BUILD)
	$(CC) $(CFLAGS) -c boot64.S -o $@

$(BUILD)/%.o: %.c mbos.h dom.h page_html.h font8x16.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(ELF): $(BUILD)/boot64.o $(COBJS:%=$(BUILD)/%) linker64.ld | $(BUILD)
	$(LD) $(LDFLAGS) -o $@ $(BUILD)/boot64.o $(COBJS:%=$(BUILD)/%)
	@echo "built $@"

run: $(ELF)
	$(QEMU) $(QEMU_COMMON) -serial stdio
serial: $(ELF)
	$(QEMU) $(QEMU_COMMON) -display none -serial stdio
test: $(ELF)
	python3 test.py

# ---- networked variant: fetch the page from the host over ARP+UDP --------
QEMU_NET := -device virtio-net-pci,netdev=n0,disable-modern=on \
            -netdev user,id=n0,net=192.168.100.0/24,host=192.168.100.1

run-net: $(ELF)
	python3 serve_page.py --daemon
	$(QEMU) $(QEMU_COMMON) $(QEMU_NET) -serial stdio
test-net: $(ELF)
	python3 test_net.py

# ---- hi-res: 1920x1080 needs more VRAM than the std default -> 64 MiB -----
# Built into its own dir/ELF so it never clashes with the default 1024x768
# objects (geometry is a -D define and make can't see flag changes).
HIRES_VGA := -vga none -device VGA,vgamem_mb=64
HELF      := $(BUILD)/mbos_hires.elf
HBUILD    := $(BUILD)/hires
HFLAGS    := -m64 -std=c11 -ffreestanding -fno-pic -mno-red-zone \
             -fno-stack-protector -fno-builtin -nostdlib -mcmodel=small -w -O2 \
             -DMBOS_GFX_W=1920 -DMBOS_GFX_H=1080
HOBJS     := $(HBUILD)/boot64.o $(COBJS:%.o=$(HBUILD)/%.o)

$(HBUILD):
	@mkdir -p $(HBUILD)
$(HBUILD)/boot64.o: boot64.S | $(HBUILD)
	$(CC) $(HFLAGS) -c boot64.S -o $@
$(HBUILD)/%.o: %.c mbos.h dom.h page_html.h font8x16.h | $(HBUILD)
	$(CC) $(HFLAGS) -c $< -o $@
$(HELF): $(HOBJS) linker64.ld | $(HBUILD)
	$(LD) $(LDFLAGS) -o $@ $(HOBJS)
	@echo "built $@ (1920x1080)"

hires: $(HELF)
	$(QEMU) -kernel $(HELF) -no-reboot $(HIRES_VGA) -serial stdio
test-hires: $(HELF)
	MBOS_ELF=$(HELF) MBOS_VGA_ARGS="$(HIRES_VGA)" python3 test.py

# ==========================================================================
# rpython render path: dom/html/render generated from rpython by py2c, linked
# into the (64-bit, graphics) kernel in place of the hand-written C.
# ==========================================================================
PY2C   := ../../../tools/py2c.py
RPYDIR := rpy
GENDIR := $(RPYDIR)/gen
RBUILD := $(BUILD)/rpy

RCFLAGS := -m64 -std=c11 -ffreestanding -fno-pic -mno-red-zone \
           -fno-stack-protector -fno-builtin -nostdlib -mcmodel=small -w -O2 \
           -DSHIVYC_ARENA_LOG2=20 -DMBOS_RPYTHON \
           -DMBOS_GFX_W=$(GFX_W) -DMBOS_GFX_H=$(GFX_H) \
           -I$(RPYDIR)/freestanding_inc -I$(RPYDIR)/gen -I.

RPY_GEN := $(GENDIR)/dom.c $(GENDIR)/htmlparse.c $(GENDIR)/render.c \
           $(GENDIR)/mbmain.c $(GENDIR)/shivyc_rt.c
RPY_KERNEL_C := main.c console.c libmini.c net.c vbe.c mbos_glue.c
RPY_OBJS := $(RBUILD)/boot64.o \
            $(RPY_KERNEL_C:%.c=$(RBUILD)/%.o) \
            $(RPY_GEN:$(GENDIR)/%.c=$(RBUILD)/gen_%.o) \
            $(RBUILD)/rt_freestanding.o
RELF := $(BUILD)/mbos_rpy.elf

$(RPYDIR)/dom.py: ../minibrowser/dom.py
	cp -f $< $@
.PHONY: transpile
transpile $(RPY_GEN): $(RPYDIR)/mbmain.py $(RPYDIR)/htmlparse.py \
                      $(RPYDIR)/render.py $(RPYDIR)/dom.py $(PY2C)
	python3 $(PY2C) $(RPYDIR)/mbmain.py $(RPYDIR)/htmlparse.py \
	    $(RPYDIR)/render.py $(RPYDIR)/dom.py --out $(GENDIR)

$(RBUILD):
	@mkdir -p $(RBUILD)
$(RBUILD)/boot64.o: boot64.S | $(RBUILD)
	$(CC) $(RCFLAGS) -c boot64.S -o $@
$(RBUILD)/%.o: %.c mbos.h dom.h page_html.h font8x16.h | $(RBUILD)
	$(CC) $(RCFLAGS) -c $< -o $@
$(RBUILD)/gen_%.o: $(GENDIR)/%.c $(RPY_GEN) | $(RBUILD)
	$(CC) $(RCFLAGS) -c $< -o $@
$(RBUILD)/rt_freestanding.o: $(RPYDIR)/rt_freestanding.c mbos.h | $(RBUILD)
	$(CC) $(RCFLAGS) -c $< -o $@

rpython: $(RELF)
$(RELF): $(RPY_OBJS) linker64.ld | $(RBUILD)
	$(LD) $(LDFLAGS) -o $@ $(RPY_OBJS)
	@echo "built $@ (rpython-generated render path)"

rpython-test: $(RELF)
	MBOS_ELF=$(RELF) python3 test.py
rpython-test-net: $(RELF)
	MBOS_ELF=$(RELF) python3 test_net.py

# ==========================================================================
# text32: the original 32-bit multiboot text-mode kernel.
# ==========================================================================
T32FLAGS := -m32 -std=c11 -ffreestanding -fno-pic -fno-stack-protector \
            -fno-builtin -nostdlib -w -O2 -DMBOS_TEXT32
T32BUILD := $(BUILD)/t32
T32OBJS  := $(T32BUILD)/boot.o \
            $(addprefix $(T32BUILD)/,main.o console_text.o libmini.o dom.o \
                                     html.o render.o net.o)
T32ELF   := $(BUILD)/mbos_text32.elf

text32: $(T32ELF)
$(T32BUILD):
	@mkdir -p $(T32BUILD)
$(T32BUILD)/boot.o: boot.S | $(T32BUILD)
	$(CC) $(T32FLAGS) -c boot.S -o $@
$(T32BUILD)/console_text.o: console_text.c mbos.h | $(T32BUILD)
	$(CC) $(T32FLAGS) -c console_text.c -o $@
$(T32BUILD)/%.o: %.c mbos.h dom.h page_html.h | $(T32BUILD)
	$(CC) $(T32FLAGS) -c $< -o $@
$(T32ELF): $(T32OBJS) linker.ld page_html.h | $(T32BUILD)
	$(LD) -m elf_i386 -T linker.ld -nostdlib -o $@ $(T32OBJS)
	@echo "built $@ (32-bit text mode)"

clean:
	rm -rf $(BUILD) page_html.h $(GENDIR)
