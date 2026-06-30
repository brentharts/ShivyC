class Mgr:
    def __init__(self, name, suppress):
        self.name = name
        self.suppress = suppress
    def __enter__(self):
        print("enter " + self.name)
        return self
    def __exit__(self, et, ev, tb):
        if ev is None:
            print("exit " + self.name + " clean")
        else:
            print("exit " + self.name + " exc=" + str(ev))
        return self.suppress

def normal():
    with Mgr("a", False):
        print("body")

def suppressed():
    with Mgr("b", True):
        print("before")
        raise ValueError("boom")
        print("after")
    print("continued after suppressed with")

def not_suppressed():
    try:
        with Mgr("c", False):
            raise ValueError("propagate")
    except ValueError as e:
        print("caught " + str(e))

def with_return():
    with Mgr("d", False):
        print("body d")
        return 42
    return 0

def exc_after_suppress():
    x = 0
    with Mgr("e", True):
        raise RuntimeError("swallow")
    x = 99
    return x

print("--normal--"); normal()
print("--suppressed--"); suppressed()
print("--not_suppressed--"); not_suppressed()
print("--return--"); print("ret", with_return())
print("--exc_after--"); print("x", exc_after_suppress())
