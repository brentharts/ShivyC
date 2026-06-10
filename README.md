### ShivyCX A C compiler created in Python.

---

ShivyCX is a C compiler written in Python 3 that supports a subset of the C11 standard and generates reasonably efficient binaries, including some optimizations. ShivyC also generates helpful compile-time error messages.


## Quickstart

### x86-64 Linux
ShivyC requires only Python 3.6 or later to compile C code. Assembling and linking are done using the GNU binutils and glibc, which you almost certainly already have installed.

## Implementation Overview
#### Preprocessor
ShivyC today has a very limited preprocessor that parses out comments and expands `#include` directives. These features are implemented between [`lexer.py`](shivyc/lexer.py) and [`preproc.py`](shivyc/lexer.py).

#### Lexer
The ShivyC lexer is implemented primarily in [`lexer.py`](shivyc/lexer.py). Additionally, [`tokens.py`](shivyc/tokens.py) contains definitions of the token classes used in the lexer and [`token_kinds.py`](shivyc/token_kinds.py) contains instances of recognized keyword and symbol tokens.

#### Parser
The ShivyC parser uses recursive descent techniques for all parsing. It is implemented in [`parser/*.py`](shivyc/parser/) and creates a parse tree of nodes defined in [`tree/*.py`](shivyc/tree/).

#### IL generation
ShivyC traverses the parse tree to generate a flat custom IL (intermediate language). The commands for this IL are in [`il_cmds/*.py`](shivyc/il_cmds/) . Objects used for IL generation are in [`il_gen.py`](shivyc/il_gen.py) , but most of the IL generating code is in the `make_code` function of each tree node in [`tree/*.py`](shivyc/tree/).

#### ASM generation
ShivyC sequentially reads the IL commands, converting each into Intel-format x86-64 assembly code. ShivyC performs register allocation using George and Appel’s iterated register coalescing algorithm (see References below). The general ASM generation functionality is in [`asm_gen.py`](shivyc/asm_gen.py) , but much of the ASM generating code is in the `make_asm` function of each IL command in [`il_cmds/*.py`](shivyc/il_cmds/).


## References
- [ShivC](https://github.com/ShivamSarodia/ShivC) - ShivyC is a rewrite from scratch of my old C compiler, ShivC, with much more emphasis on feature completeness and code quality. See the ShivC README for more details.
- C11 Specification - http://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf
- x86_64 ABI - https://github.com/hjl-tools/x86-psABI/wiki/x86-64-psABI-1.0.pdf
- Iterated Register Coalescing (George and Appel) - https://www.cs.purdue.edu/homes/hosking/502/george.pdf
