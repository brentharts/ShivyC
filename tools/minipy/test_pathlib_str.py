# Runtime-surface features for self-hosting py2c: frozenset, __str__ dispatch in
# str()/print, and __truediv__ operator dispatch. Uses minipy's own classes so
# the three backends are directly comparable (the pathlib shim itself is
# exercised separately, since it stands in for CPython's real pathlib).

F = frozenset({"a", "b", "c"})
print("a" in F, "z" in F, len(F))
G = frozenset()
print(len(G))


class Money:
    def __init__(self, cents):
        self.cents = cents
    def __str__(self):
        return "$" + str(self.cents // 100)

m = Money(500)
print(m)
print(str(m))
print("cost is " + str(m))
print([str(Money(100)), str(Money(250))])


class Route:
    def __init__(self, s):
        self.s = s
    def __truediv__(self, other):
        return Route(self.s + "/" + other)
    def __str__(self):
        return self.s

r = Route("a") / "b" / "c" / "d"
print(str(r))
print(r)
