"""Regression test for the packed-call-operand overflow (see MINIPY.md).

CALL_METHOD packs `name_const_index * 256 + nargs` and CALL_FUNC packs
`func_index * 256 + nargs` into the instruction's 16-bit `c` field, so both
indices must be < 256. The compiler used to guard the method form with
`cn < (1 << 23)` and never bounds-checked the func index at all, so once a
module's constant pool grew past 255 entries, a method call whose name-const
landed at index >= 256 overflowed the 16-bit field, wrapped, and the native
interpreter dispatched through a garbage const -- a pointer-flood crash the
pure-Python reference VM never exhibited.

This program forces the constant pool well past 256 (260+ distinct string
literals) and *then* performs a method call, so the method-name const is
interned at a high index. Under the bug the native run diverges from CPython;
with the fix all three executors agree.

Run three-way:
    python3 tools/minipy/test_const_overflow.py     # CPython ground truth
    python3 tools/rpy.py --ref  tools/minipy/test_const_overflow.py
    python3 tools/rpy.py        tools/minipy/test_const_overflow.py
All three must print the same thing.
"""


class Counter:
    def __init__(self):
        self.n = 0

    def bump(self, k):
        self.n = self.n + k
        return self.n


def big_pool():
    # 270 distinct string constants -> the constant pool blows past 256 before
    # any of the method-call name-consts below are interned.
    vals = [
        "c000", "c001", "c002", "c003", "c004", "c005", "c006", "c007", "c008", "c009",
        "c010", "c011", "c012", "c013", "c014", "c015", "c016", "c017", "c018", "c019",
        "c020", "c021", "c022", "c023", "c024", "c025", "c026", "c027", "c028", "c029",
        "c030", "c031", "c032", "c033", "c034", "c035", "c036", "c037", "c038", "c039",
        "c040", "c041", "c042", "c043", "c044", "c045", "c046", "c047", "c048", "c049",
        "c050", "c051", "c052", "c053", "c054", "c055", "c056", "c057", "c058", "c059",
        "c060", "c061", "c062", "c063", "c064", "c065", "c066", "c067", "c068", "c069",
        "c070", "c071", "c072", "c073", "c074", "c075", "c076", "c077", "c078", "c079",
        "c080", "c081", "c082", "c083", "c084", "c085", "c086", "c087", "c088", "c089",
        "c090", "c091", "c092", "c093", "c094", "c095", "c096", "c097", "c098", "c099",
        "c100", "c101", "c102", "c103", "c104", "c105", "c106", "c107", "c108", "c109",
        "c110", "c111", "c112", "c113", "c114", "c115", "c116", "c117", "c118", "c119",
        "c120", "c121", "c122", "c123", "c124", "c125", "c126", "c127", "c128", "c129",
        "c130", "c131", "c132", "c133", "c134", "c135", "c136", "c137", "c138", "c139",
        "c140", "c141", "c142", "c143", "c144", "c145", "c146", "c147", "c148", "c149",
        "c150", "c151", "c152", "c153", "c154", "c155", "c156", "c157", "c158", "c159",
        "c160", "c161", "c162", "c163", "c164", "c165", "c166", "c167", "c168", "c169",
        "c170", "c171", "c172", "c173", "c174", "c175", "c176", "c177", "c178", "c179",
        "c180", "c181", "c182", "c183", "c184", "c185", "c186", "c187", "c188", "c189",
        "c190", "c191", "c192", "c193", "c194", "c195", "c196", "c197", "c198", "c199",
        "c200", "c201", "c202", "c203", "c204", "c205", "c206", "c207", "c208", "c209",
        "c210", "c211", "c212", "c213", "c214", "c215", "c216", "c217", "c218", "c219",
        "c220", "c221", "c222", "c223", "c224", "c225", "c226", "c227", "c228", "c229",
        "c230", "c231", "c232", "c233", "c234", "c235", "c236", "c237", "c238", "c239",
        "c240", "c241", "c242", "c243", "c244", "c245", "c246", "c247", "c248", "c249",
        "c250", "c251", "c252", "c253", "c254", "c255", "c256", "c257", "c258", "c259",
        "c260", "c261", "c262", "c263", "c264", "c265", "c266", "c267", "c268", "c269",
    ]
    return len(vals)


def main():
    pool = big_pool()
    c = Counter()
    # These method calls' name-const ("bump") is interned at a high index,
    # AFTER the 270-entry pool above -- the exact overflow condition.
    total = 0
    total = c.bump(3)
    total = c.bump(4)
    total = c.bump(5)
    print(pool)
    print(total)


main()
