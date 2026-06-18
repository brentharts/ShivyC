# rpython Mandelbrot -> PPM

`mandelbrot.py` renders the Mandelbrot set to a 256x256 binary PPM (P6),
lowered to C with **no runtime**. It exercises several rpython features at once:

* **float-local inference** — `x`, `y`, `xt`, `x0`, `y0` are all proven `double`
  from the literals and arithmetic (the escape iteration is pure FP).
* **typed C arrays** — the RGB byte buffer is a `char*` from `malloc`, written
  with native byte stores (`buf[idx] = ...`).
* **binary file I/O** — `f.write(str)` -> `fputs` for the header, and the new
  two-argument `f.write(buf, n)` -> `fwrite` for the raw pixel block.

The exit code is a checksum of the escape counts, so the render is verifiable
without reading the file back.

```
python3 -m shivyc.main --no-cache mandelbrot.py -o /tmp/mandel && /tmp/mandel
#  -> /tmp/mandel.ppm   (open it, or convert: pnmtopng /tmp/mandel.ppm > out.png)
```
