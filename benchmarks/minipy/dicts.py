d = {}
i = 0
while i < 40000:
    k = (i * 2654435761) % 20000
    if k in d:
        d[k] = d[k] + 1
    else:
        d[k] = 1
    i = i + 1
total = 0
for k in d:
    total = total + d[k]
print(total, len(d))
