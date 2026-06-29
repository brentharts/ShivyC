class Boom(Exception):
    pass

def normal():
    try:
        print("try")
    finally:
        print("finally")

def with_return():
    try:
        print("try")
        return 1
    finally:
        print("finally")

def with_exc():
    try:
        try:
            print("try")
            raise Boom("b")
        finally:
            print("finally")
    except Boom:
        print("caught")

def try_except_finally():
    try:
        print("try")
        raise Boom("k")
    except Boom:
        print("except")
    finally:
        print("finally")

def in_loop():
    i = 0
    while i < 3:
        try:
            if i == 1:
                i = i + 1
                continue
            if i == 2:
                break
            print("body " + str(i))
        finally:
            print("fin " + str(i))
        i = i + 1
    print("after loop")

def nested_return():
    try:
        try:
            return 7
        finally:
            print("inner")
    finally:
        print("outer")

print("--normal--"); normal()
print("--return--"); print("ret", with_return())
print("--exc--"); with_exc()
print("--tef--"); try_except_finally()
print("--loop--"); in_loop()
print("--nested--"); print("ret", nested_return())
