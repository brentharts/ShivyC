# Repeated dot product of two integer arrays (baseline, no annotations).
a : list[int] = []
b : list[int] = []
i = 0
while i < 2000:
    a.append((i * 31 + 7) % 1000)
    b.append((i * 17 + 3) % 1000)
    i = i + 1
total = 0
r = 0
while r < 1200:
    k = 0
    while k < 2000:
        total = total + a[k] * b[k]
        k = k + 1
    r = r + 1
print(total)
