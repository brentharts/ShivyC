# Repeated reduction over an integer array (baseline, no annotations).
arr = []
i = 0
while i < 2000:
    arr.append((i * 31 + 7) % 1000)
    i = i + 1
total = 0
r = 0
while r < 4000:
    k = 0
    while k < 2000:
        total = total + arr[k]
        k = k + 1
    r = r + 1
print(total)
