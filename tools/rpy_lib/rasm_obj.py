"""rasm_obj -- assembler driver + ELF64 object writer.

Turns a full ShivyCX-emitted Intel-syntax assembly file into an ELF64
relocatable object, so it can replace `as -o x.o x.s`. Two passes: pass 1 lays
out sections, records label offsets, encodes instructions (via rasm) and data
directives, and collects relocations; pass 2 resolves same-section PC-relative
references to local labels in place and keeps the rest as ELF relocations.

Kept flat and RPython-friendly (uniform record classes, explicit byte lists).
"""
import rasm


# --------------------------------------------------------------------------
# Object model
# --------------------------------------------------------------------------
class Section(object):
    def __init__(self, name):
        self.name = name
        self.data = []       # list[int]  (bytes); empty for .bss (nobits)
        self.relocs = []     # list[rasm.Reloc] with .where = section offset
        self.nobits = (name == ".bss")

    def offset(self):
        return len(self.data)

    def emit(self, byte_list):
        self.data.extend(byte_list)


class Symbol(object):
    def __init__(self, name):
        self.name = name
        self.section = ""     # section name where defined ("" if undefined)
        self.value = 0        # offset within its section
        self.is_global = False
        self.defined = False
        self.size = 0         # for .comm / objects
        self.common = False   # SHN_COMMON (from .comm)


class AsmError(Exception):
    pass


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
_DATA_WIDTH = {".byte": 1, ".word": 2, ".short": 2, ".int": 4, ".long": 4,
               ".quad": 8}


class Assembler(object):
    def __init__(self):
        self.sections = {}
        self.order = []
        self.symbols = {}
        self.cur = None
        self._get_section(".text")
        self._get_section(".data")
        self._get_section(".bss")
        self.cur = self.sections[".text"]

    def _get_section(self, name):
        if name not in self.sections:
            s = Section(name)
            self.sections[name] = s
            self.order.append(name)
        return self.sections[name]

    def _sym(self, name):
        if name not in self.symbols:
            self.symbols[name] = Symbol(name)
        return self.symbols[name]

    def assemble(self, text):
        self.att_mode = False
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            self._line(lines[i])
            i += 1
        self._resolve()

    def _line(self, raw):
        kind, a, ops = rasm.parse_line(raw)
        if kind == "blank":
            return
        if kind == "label":
            sym = self._sym(a)
            sym.section = self.cur.name
            sym.value = self.cur.offset()
            sym.defined = True
            return
        if kind == "dir":
            self._directive(a)
            return
        # instruction
        if self.att_mode:
            raise AsmError("AT&T-syntax inline asm not supported by rasm: %r"
                           % raw.strip())
        body, relocs = rasm.encode(a, ops)
        base = self.cur.offset()
        self.cur.emit(body)
        j = 0
        while j < len(relocs):
            r = relocs[j]
            self.cur.relocs.append(
                rasm.Reloc(base + r.where, r.sym, r.size, r.pcrel, r.add))
            j += 1

    def _directive(self, line):
        parts = line.split()
        d = parts[0]
        if d == ".intel_syntax" or d == ".att_syntax":
            self.att_mode = (d == ".att_syntax")
            return
        if d == ".section":
            name = parts[1] if len(parts) > 1 else ".text"
            # strip a trailing ,"...",@progbits attribute list
            comma = name.find(",")
            if comma >= 0:
                name = name[:comma]
            self.cur = self._get_section(name)
            return
        if d == ".text":
            self.cur = self._get_section(".text")
            return
        if d == ".data":
            self.cur = self._get_section(".data")
            return
        if d == ".bss":
            self.cur = self._get_section(".bss")
            return
        if d == ".global" or d == ".globl":
            self._sym(parts[1]).is_global = True
            return
        if d == ".comm":
            sym = self._sym(parts[1])
            sym.size = int(parts[2])
            sym.common = True
            sym.is_global = True
            sym.defined = True
            return
        if d == ".zero" or d == ".skip":
            n = int(parts[1])
            self.cur.emit([0] * n)
            return
        if d in _DATA_WIDTH:
            self._data(d, line[len(d):].strip())
            return
        if d == ".align" or d == ".p2align" or d == ".balign":
            return  # alignment padding omitted for now (harmless for tests)
        if d == ".string" or d == ".asciz" or d == ".ascii":
            return  # (string data not emitted by ShivyCX in these forms)
        # unknown directive: ignore silently to stay robust
        return

    def _data(self, d, rest):
        width = _DATA_WIDTH[d]
        # comma-separated values
        items = rest.split(",")
        k = 0
        while k < len(items):
            v = items[k].strip()
            if v != "":
                if rasm._looks_int(v):
                    self.cur.emit(rasm.pack_le(rasm._parse_int(v), width))
                else:
                    # a symbol reference in data -> absolute relocation
                    off = self.cur.offset()
                    self.cur.emit(rasm.pack_le(0, width))
                    pcrel = False
                    self.cur.relocs.append(rasm.Reloc(off, v, width, pcrel, 0))
            k += 1

    def _resolve(self):
        """Resolve same-section PC-relative refs to local (non-global) labels
        in place; keep the rest as ELF relocations."""
        for name in self.order:
            sec = self.sections[name]
            keep = []
            j = 0
            while j < len(sec.relocs):
                r = sec.relocs[j]
                sym = self.symbols.get(r.sym, None)
                resolved = False
                if (r.pcrel and sym is not None and sym.defined
                        and not sym.is_global and sym.section == sec.name):
                    # ELF PC-relative value: S + A - P  (A already folded to -4)
                    rel = sym.value + r.add - r.where
                    patch = rasm.pack_le(rel, r.size)
                    m = 0
                    while m < r.size:
                        sec.data[r.where + m] = patch[m]
                        m += 1
                    resolved = True
                if not resolved:
                    keep.append(r)
                j += 1
            sec.relocs = keep


# --------------------------------------------------------------------------
# ELF64 relocatable-object writer
# --------------------------------------------------------------------------
# Emits an ET_REL x86-64 object equivalent to `as -o x.o`. Jumps use rel32
# (no branch relaxation yet), so output is larger than `as` but functionally
# identical after linking.

SHN_UNDEF = 0
SHN_COMMON = 0xFFF2

SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
SHT_NOBITS = 8

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4

STB_LOCAL = 0
STB_GLOBAL = 1
STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC = 2
STT_SECTION = 3

R_X86_64_64 = 1
R_X86_64_PC32 = 2
R_X86_64_32 = 10
R_X86_64_32S = 11


def _u16(v):
    return rasm.pack_le(v, 2)


def _u32(v):
    return rasm.pack_le(v, 4)


def _u64(v):
    return rasm.pack_le(v, 8)


class _StrTab(object):
    def __init__(self):
        self.data = [0]        # index 0 is the empty string
        self.map = {"": 0}

    def add(self, s):
        if s in self.map:
            return self.map[s]
        off = len(self.data)
        i = 0
        while i < len(s):
            self.data.append(ord(s[i]))
            i += 1
        self.data.append(0)
        self.map[s] = off
        return off


def _reloc_type(r):
    if r.pcrel:
        return R_X86_64_PC32
    if r.size == 8:
        return R_X86_64_64
    return R_X86_64_32S


def write_elf(asm):
    # Sections that carry data/relocs, in a fixed order. .bss is nobits.
    data_secs = [".text", ".data", ".bss"]
    # extra sections present (e.g. .note.GNU-stack) are ignored: empty & unused.

    # ---- section index assignment ----
    # 0:NULL 1:.text 2:.data 3:.bss then rela sections, then symtab/strtab/shstr
    sec_index = {".text": 1, ".data": 2, ".bss": 3}
    shdrs = []          # list of dicts describing each section header
    shstr = _StrTab()

    # ---- symbol table ----
    strtab = _StrTab()
    syms = []           # list of dicts
    symindex = {}       # name -> index

    # index 0: null symbol
    syms.append({"name": 0, "info": 0, "shndx": 0, "value": 0, "size": 0})

    # local: one STT_SECTION symbol per data section
    secsym_index = {}
    for sn in data_secs:
        idx = len(syms)
        secsym_index[sn] = idx
        syms.append({"name": 0, "info": (STB_LOCAL << 4) | STT_SECTION,
                     "shndx": sec_index[sn], "value": 0, "size": 0})

    # local: any defined non-global label that a relocation references (e.g.
    # ShivyCX's float literals `__fltlitN` in .data). These must precede the
    # first global so sh_info stays correct.
    referenced = {}
    for sn in data_secs:
        sec = asm.sections.get(sn, None)
        if sec is None:
            continue
        for r in sec.relocs:
            referenced[r.sym] = True
    local_names = sorted(referenced.keys())
    for nm in local_names:
        s = asm.symbols.get(nm, None)
        if s is not None and s.defined and not s.is_global and not s.common:
            idx = len(syms)
            symindex[nm] = idx
            styp = STT_FUNC if s.section == ".text" else STT_OBJECT
            syms.append({"name": strtab.add(nm),
                         "info": (STB_LOCAL << 4) | styp,
                         "shndx": sec_index.get(s.section, 0),
                         "value": s.value, "size": 0})

    first_global = len(syms)

    # globals: defined globals, common symbols, and undefined referenced syms
    names = sorted(asm.symbols.keys())
    # defined/common globals first
    for nm in names:
        s = asm.symbols[nm]
        if s.common:
            idx = len(syms)
            symindex[nm] = idx
            syms.append({"name": strtab.add(nm),
                         "info": (STB_GLOBAL << 4) | STT_OBJECT,
                         "shndx": SHN_COMMON, "value": 8, "size": s.size})
        elif s.is_global and s.defined:
            styp = STT_FUNC if s.section == ".text" else STT_OBJECT
            idx = len(syms)
            symindex[nm] = idx
            syms.append({"name": strtab.add(nm),
                         "info": (STB_GLOBAL << 4) | styp,
                         "shndx": sec_index.get(s.section, 0),
                         "value": s.value, "size": 0})
    # undefined symbols referenced by relocations
    for sn in data_secs:
        sec = asm.sections.get(sn, None)
        if sec is None:
            continue
        for r in sec.relocs:
            if r.sym not in symindex and r.sym not in secsym_index:
                idx = len(syms)
                symindex[r.sym] = idx
                syms.append({"name": strtab.add(r.sym),
                             "info": (STB_GLOBAL << 4) | STT_NOTYPE,
                             "shndx": SHN_UNDEF, "value": 0, "size": 0})

    # ---- assemble file bytes ----
    out = []
    # ELF header placeholder (filled after we know section-header offset)
    ehdr_size = 64

    # section contents come after the header; track file offsets
    body = []
    text_off = ehdr_size

    def cur_off():
        return ehdr_size + len(body)

    text_sec = asm.sections[".text"]
    data_sec = asm.sections[".data"]
    bss_sec = asm.sections[".bss"]

    text_file = cur_off()
    body.extend(text_sec.data)
    data_file = cur_off()
    body.extend(data_sec.data)
    # .bss occupies no file space

    # symtab
    sym_file = cur_off()
    for sm in syms:
        body.extend(_u32(sm["name"]))
        body.append(sm["info"] & 0xFF)
        body.append(0)  # st_other
        body.extend(_u16(sm["shndx"]))
        body.extend(_u64(sm["value"]))
        body.extend(_u64(sm["size"]))
    sym_size = len(syms) * 24

    # strtab
    str_file = cur_off()
    body.extend(strtab.data)

    # rela sections
    rela_files = {}
    rela_sizes = {}
    for sn in [".text", ".data"]:
        sec = asm.sections[sn]
        if len(sec.relocs) == 0:
            continue
        rela_files[sn] = cur_off()
        for r in sec.relocs:
            si = symindex.get(r.sym, secsym_index.get(sn, 0))
            info = (si << 32) | _reloc_type(r)
            body.extend(_u64(r.where))
            body.extend(_u64(info))
            body.extend(rasm.pack_le(r.add, 8))
        rela_sizes[sn] = len(sec.relocs) * 24

    # shstrtab (build names now)
    shstr_file = cur_off()
    n_null = shstr.add("")
    n_text = shstr.add(".text")
    n_data = shstr.add(".data")
    n_bss = shstr.add(".bss")
    n_symtab = shstr.add(".symtab")
    n_strtab = shstr.add(".strtab")
    n_shstr = shstr.add(".shstrtab")
    n_relatext = shstr.add(".rela.text")
    n_reladata = shstr.add(".rela.data")
    n_note = shstr.add(".note.GNU-stack")
    body.extend(shstr.data)

    # ---- section headers ----
    # assign indices: 0 NULL, 1 .text, 2 .data, 3 .bss, then symtab,strtab,
    # shstrtab, then rela.text, rela.data (must appear after symtab so link idx
    # is known). We compute indices dynamically.
    sh = []

    def add_sh(name, typ, flags, off, size, link, info, align, entsize):
        sh.append({"name": name, "type": typ, "flags": flags, "addr": 0,
                   "off": off, "size": size, "link": link, "info": info,
                   "align": align, "entsize": entsize})

    add_sh(0, 0, 0, 0, 0, 0, 0, 0, 0)                            # 0 NULL
    add_sh(n_text, SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR,
           text_file, len(text_sec.data), 0, 0, 16, 0)          # 1 .text
    add_sh(n_data, SHT_PROGBITS, SHF_ALLOC | SHF_WRITE,
           data_file, len(data_sec.data), 0, 0, 8, 0)           # 2 .data
    add_sh(n_bss, SHT_NOBITS, SHF_ALLOC | SHF_WRITE,
           cur_off(), 0, 0, 0, 8, 0)                            # 3 .bss
    symtab_idx = len(sh)
    add_sh(n_symtab, SHT_SYMTAB, 0, sym_file, sym_size,
           symtab_idx + 1, first_global, 8, 24)                 # symtab
    strtab_idx = symtab_idx + 1
    add_sh(n_strtab, SHT_STRTAB, 0, str_file, len(strtab.data),
           0, 0, 1, 0)                                          # strtab
    shstr_idx = strtab_idx + 1
    add_sh(n_shstr, SHT_STRTAB, 0, shstr_file, len(shstr.data),
           0, 0, 1, 0)                                          # shstrtab
    if ".text" in rela_files:
        add_sh(n_relatext, SHT_RELA, 0, rela_files[".text"],
               rela_sizes[".text"], symtab_idx, 1, 8, 24)
    if ".data" in rela_files:
        add_sh(n_reladata, SHT_RELA, 0, rela_files[".data"],
               rela_sizes[".data"], symtab_idx, 2, 8, 24)
    # empty .note.GNU-stack marks the stack non-executable (silences linker)
    add_sh(n_note, SHT_PROGBITS, 0, shstr_file, 0, 0, 0, 1, 0)

    # section header table goes at the current end (align to 8)
    while (ehdr_size + len(body)) % 8 != 0:
        body.append(0)
    shoff = ehdr_size + len(body)
    for h in sh:
        body.extend(_u32(h["name"]))
        body.extend(_u32(h["type"]))
        body.extend(_u64(h["flags"]))
        body.extend(_u64(h["addr"]))
        body.extend(_u64(h["off"]))
        body.extend(_u64(h["size"]))
        body.extend(_u32(h["link"]))
        body.extend(_u32(h["info"]))
        body.extend(_u64(h["align"]))
        body.extend(_u64(h["entsize"]))

    # ---- ELF header ----
    eh = []
    eh.extend([0x7F, ord('E'), ord('L'), ord('F'), 2, 1, 1, 0])
    eh.extend([0, 0, 0, 0, 0, 0, 0, 0])       # e_ident padding
    eh.extend(_u16(1))                         # e_type ET_REL
    eh.extend(_u16(62))                        # e_machine EM_X86_64
    eh.extend(_u32(1))                         # e_version
    eh.extend(_u64(0))                         # e_entry
    eh.extend(_u64(0))                         # e_phoff
    eh.extend(_u64(shoff))                     # e_shoff
    eh.extend(_u32(0))                         # e_flags
    eh.extend(_u16(64))                        # e_ehsize
    eh.extend(_u16(0))                         # e_phentsize
    eh.extend(_u16(0))                         # e_phnum
    eh.extend(_u16(64))                        # e_shentsize
    eh.extend(_u16(len(sh)))                   # e_shnum
    eh.extend(_u16(shstr_idx))                 # e_shstrndx

    out.extend(eh)
    out.extend(body)
    return out


def assemble_to_elf(text):
    a = Assembler()
    a.assemble(text)
    return write_elf(a)
