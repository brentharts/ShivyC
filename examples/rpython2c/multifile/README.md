# multifile — several rpython sources as one translation unit

`app.py` imports `geom.py`; building them together

```
python3 -m shivyc.main app.py geom.py -o app
./app    # exits 38  (area(4,5)=20  +  perimeter(4,5)=18)
```

translates both as a single unit: the py2c runtime is emitted once, `from geom
import ...` resolves `geom` against the input files' directory, and the calls
lower to direct C calls into geom's code. No on-disk AST cache and no dynamic
import are involved — the whole call graph is visible in one invocation.
