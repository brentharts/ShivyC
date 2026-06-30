def t_value():
    try:
        raise ValueError("bad")
    except ValueError:
        print("got ValueError")

def t_hierarchy():
    try:
        raise KeyError("k")
    except Exception:
        print("KeyError caught as Exception")

def t_specific_first():
    try:
        raise IndexError("i")
    except KeyError:
        print("wrong: KeyError")
    except IndexError:
        print("got IndexError")
    except Exception:
        print("wrong: Exception")

def t_bare():
    try:
        raise RuntimeError
    except RuntimeError:
        print("got bare RuntimeError")

class MyErr(ValueError):
    pass

def t_user_derives_builtin():
    try:
        raise MyErr("custom")
    except ValueError:
        print("MyErr caught as ValueError")

def t_propagate():
    try:
        try:
            raise TypeError("t")
        except ValueError:
            print("wrong")
    except TypeError:
        print("propagated to TypeError")

t_value()
t_hierarchy()
t_specific_first()
t_bare()
t_user_derives_builtin()
t_propagate()
