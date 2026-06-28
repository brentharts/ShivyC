def make(n):
    xs = []
    seed = 12345
    i = 0
    while i < n:
        seed = (seed * 1103515245 + 12345) % 2147483648
        xs.append(seed % 100000)
        i = i + 1
    return xs

def insort(xs):
    n = len(xs)
    i = 1
    while i < n:
        key = xs[i]
        j = i - 1
        while j >= 0 and xs[j] > key:
            xs[j + 1] = xs[j]
            j = j - 1
        xs[j + 1] = key
        i = i + 1
    return xs

a = make(900)
insort(a)
print(a[0], a[450], a[899], len(a))
