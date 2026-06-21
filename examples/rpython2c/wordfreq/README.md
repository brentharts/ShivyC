# wordfreq — realistic dict usage

The two most common real-world dict patterns:

- **Frequency counting** with `d[k] = d.get(k, 0) + 1`.
- **Grouping** items into a dict whose values are lists (a dict-of-lists), built
  with the `if key not in groups: groups[key] = []` / `groups[key].append(item)`
  idiom.

Together these exercise `get` with a default, `in`, key iteration, and storing
mutable lists as dict values. CPython, `gcc`, and ShivyCX-self-compiled all exit
**93**.
