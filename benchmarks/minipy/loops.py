total = 0
i = 0
while i < 1000000:
    total = total + (i * 3 - 1) % 7
    i = i + 1
print(total)
