def clen(n):
    steps = 0
    while n != 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
        steps = steps + 1
    return steps

best = 0
besti = 0
i = 1
while i < 12000:
    c = clen(i)
    if c > best:
        best = c
        besti = i
    i = i + 1
print(besti, best)
