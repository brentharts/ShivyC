class Err(Exception):
    pass

def show(e):
    print("str=" + str(e))

try:
    raise ValueError("bad value")
except ValueError as e:
    show(e)
    print("args0=" + str(e.args[0]))

try:
    raise Err("deep msg")
except Exception as e:
    show(e)

try:
    raise KeyError("mykey")
except KeyError as e:
    print("kstr=" + str(e))

try:
    raise RuntimeError()
except RuntimeError as e:
    print("empty=[" + str(e) + "]")

try:
    raise ValueError("a", "b")
except ValueError as e:
    print("multi=" + str(e))
    print("nargs=" + str(len(e.args)))
