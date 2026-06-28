"""rpy -- CPython-side helpers for rpython (py2c) code generation.

This module is **CPython-only**. It is never translated to C: py2c.py does not
compile `import rpy`; it only *recognizes* the call `rpy.json.generate_decoder(Cls)`
(and `generate_encoder`) used as an argument and, knowing `Cls`'s field layout,
emits a specialized C parser that builds the POD struct directly -- no dict, no
boxing, no Python callback.

Running the same source under plain CPython must still work and produce the same
objects, so the functions here implement faithful runtime behavior:

    import json, rpy
    class User:
        def __init__(self, name: "char*", age: "int"):
            self.name = name
            self.age = age

    hook = rpy.json.generate_decoder(User)
    u = json.loads('{"name": "ada", "age": 36}', object_hook=hook)   # -> User

The encoder side lets a plain-Python *server* emit data an rpython *client* can
read with the matching generated parser:

    enc = rpy.json.generate_encoder(User)
    json.dumps(User("ada", 36), default=enc)   # -> '{"name": "ada", "age": 36}'
"""

import inspect


def _ctor_fields(cls):
    """Ordered constructor field names of `cls` (its __init__ params minus
    self). These are the slots py2c lays out in the POD struct, in order."""
    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return []
    names = list(sig.parameters)
    return names[1:] if names and names[0] == "self" else names


def _ctor_annotations(cls):
    """Map constructor field name -> its annotation (as written). Used to find
    class-typed fields (e.g. `addr: "Addr"`) for nested decoding."""
    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return {}
    return {n: p.annotation for n, p in sig.parameters.items()
            if p.annotation is not inspect.Parameter.empty}


def _ann_class(cls, ann):
    """If annotation `ann` (a string like "Addr") names a class reachable from
    where `cls` is defined, return that class; else None. Lets the decoder build
    nested objects without the caller wiring them up explicitly."""
    if not isinstance(ann, str):
        return ann if isinstance(ann, type) else None
    name = ann.strip().strip("'\"").rstrip("*").strip()
    g = getattr(cls.__init__, "__globals__", {})
    obj = g.get(name)
    return obj if isinstance(obj, type) else None


def _ann_list_class(cls, ann):
    """If annotation `ann` is `list[Cls]` / `List[Cls]` naming a known class,
    return that class; else None. Mirrors the generated decoder building a list
    of nested objects."""
    if not isinstance(ann, str):
        return None
    a = ann.strip().strip("'\"")
    if "[" not in a:
        return None
    head, _, rest = a.partition("[")
    if head.strip() not in ("list", "List"):
        return None
    elem = rest.rsplit("]", 1)[0].strip()
    return _ann_class(cls, elem)


def _build(cls, dct):
    """Recursively construct `cls` from dict `dct`: a constructor field whose
    annotation names a class (value a dict) is built recursively, and a
    `list[Cls]` field (value a list) builds each element. Mirrors the generated
    C decoder, which calls the nested class's decoder inline / per array element.
    """
    args = []
    anns = _ctor_annotations(cls)
    for f in _ctor_fields(cls):
        v = dct[f]
        ann = anns.get(f)
        sub = _ann_class(cls, ann)
        if sub is not None and isinstance(v, dict):
            v = _build(sub, v)
        else:
            elem = _ann_list_class(cls, ann)
            if elem is not None and isinstance(v, list):
                v = [_build(elem, e) if isinstance(e, dict) else e for e in v]
        args.append(v)
    return cls(*args)


class _Json:
    """Namespace exposed as `rpy.json`."""

    def generate_decoder(self, cls):
        """Return a json `object_hook` that builds `cls` from a parsed dict,
        recursively constructing nested class-typed fields.

        Under CPython this is a real callable used by `json.loads`. Under py2c
        the *call* `rpy.json.generate_decoder(cls)` is intercepted: the
        translator reads `cls`'s fields and generates a C parser instead, so the
        returned function is never actually invoked in compiled code.

        Note on object_hook semantics: json calls the hook bottom-up, so a nested
        object's dict reaches this hook first. It is returned unchanged (it does
        not have the *root* class's fields), then converted when the enclosing
        object is built -- matching how the generated parser recurses top-down.
        """
        fields = _ctor_fields(cls)

        def object_hook(dct):
            for f in fields:
                if f not in dct:
                    return dct      # not a `cls`-shaped object; leave as dict
            return _build(cls, dct)

        return object_hook

    def generate_encoder(self, cls):
        """Return a json `default` function that serializes `cls` instances to a
        plain object with its constructor fields (nested class instances are
        serialized recursively by json once this default is applied). For a
        Python server feeding an rpython client."""
        def default(obj):
            fields = _ctor_fields(type(obj))
            if fields:
                return {f: getattr(obj, f) for f in fields}
            raise TypeError(
                "Object of type %s is not JSON serializable"
                % type(obj).__name__)

        return default


json = _Json()


class _Threads:
    """Namespace exposed as ``rpy.threads`` for ShivyCX's register-partitioned
    bare-metal threads.

    Under CPython the decorators are identity wrappers (they only record the
    side/core on the function for introspection) and ``start_new_thread`` spawns
    a real OS thread via ``_thread.start_new_thread`` -- so the same source runs,
    semi-faithfully, on the host.

    Under py2c the ``@rpy.threads.left(core=N)`` / ``.right(core=N)`` decorator
    is *recognized*: the translator strips it and emits the equivalent
    ``assert FN in threads.left(core=N)`` partition contract in ``main``'s header
    (guarded by ``#ifdef __SHIVYC__`` so gcc still accepts the C), and a
    ``rpy.threads.start_new_thread(fn)`` call lowers to a direct ``fn()``. The
    contract is what ShivyCX's thread-partition analysis reads to split the
    register file between the two threads (see shivyc/thread_contracts.py).
    """

    def left(self, core=0):
        def deco(fn):
            fn.__rpy_thread__ = ("left", core)
            return fn
        return deco

    def right(self, core=0):
        def deco(fn):
            fn.__rpy_thread__ = ("right", core)
            return fn
        return deco

    def start_new_thread(self, fn, args=()):
        """Spawn `fn(*args)` on a new OS thread (CPython). The translator instead
        lowers `start_new_thread(fn)` to a direct call `fn()`."""
        import _thread
        return _thread.start_new_thread(fn, tuple(args))


threads = _Threads()
