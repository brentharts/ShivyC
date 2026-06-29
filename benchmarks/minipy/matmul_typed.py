# Typed variant of matmul.py. The annotations are ordinary Python (ignored by
# CPython) but let minipy infer that A and B are matrices of fixed-size int rows,
# so the inner multiply-accumulate fuses into a single specialised opcode.
n = 28
A : list[ list[int:n] ] = []
B : list[ list[int:n] ] = []
i = 0
while i < n:
    rowa = []
    rowb = []
    j = 0
    while j < n:
        rowa.append((i * 7 + j) % 11)
        rowb.append((i + j * 5) % 13)
        j = j + 1
    A.append(rowa)
    B.append(rowb)
    i = i + 1
C = 0
i = 0
while i < n:
    j = 0
    while j < n:
        s = 0
        k = 0
        while k < n:
            s = s + A[i][k] * B[k][j]
            k = k + 1
        C = C + s % 7
        j = j + 1
    i = i + 1
print(C)
