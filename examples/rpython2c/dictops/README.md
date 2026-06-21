# dictops — a tour of dict operations

Dicts are a first-class runtime type (`T_DICT`): an insertion-ordered array of
key/value entries with linear lookup by `obj_eq`. Keys and values are boxed
`obj`s, so a single dict can mix scalar key/value types (ints, strings, bools).

Supported in rpython sources:

- literals `{k: v, ...}` and comprehensions `{k: v for ... in ...}`
- `d[k]` read and write, and `d[k] += …` augmented update
- `get(k[, default])`, `setdefault(k[, default])`, `pop(k[, default])`,
  `update(other)`, `clear()`, `copy()`
- `del d[k]`, `k in d`, `len(d)`
- `keys()`, `values()`, `items()`, and direct iteration `for k in d`
- `d1 | d2` merge (non-mutating; right operand wins on conflicts)
- order-independent equality `==`

This recently gained `copy()`, `|` merge, and `==` (previously `copy` failed to
compile, `|` fell through to a bitwise no-op, and `==` compared identities).

CPython, `gcc`, and ShivyCX-self-compiled all exit **186**.
