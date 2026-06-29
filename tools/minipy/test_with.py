class Boom(Exception):
    pass

class CM:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        print("enter " + self.name)
        return self.name
    def __exit__(self, a, b, c):
        print("exit " + self.name)

def basic():
    with CM("x") as v:
        print("body " + v)

def no_as():
    with CM("y"):
        print("body2")

def with_exc():
    try:
        with CM("z"):
            print("before")
            raise Boom("k")
            print("after")
    except Boom:
        print("caught")

def with_return():
    with CM("r") as v:
        print("body3")
        return v
    print("unreached")

def multi():
    with CM("a") as p, CM("b") as q:
        print("body4 " + p + q)

def nested():
    with CM("out"):
        with CM("in"):
            print("inner body")

print("--basic--"); basic()
print("--no_as--"); no_as()
print("--exc--"); with_exc()
print("--return--"); print("ret", with_return())
print("--multi--"); multi()
print("--nested--"); nested()
