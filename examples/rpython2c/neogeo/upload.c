/* Neo-Geo VRAM / palette upload -- the on-target side of the loading screen.
 *
 * This is the code the baked scene data (neogeo_scene.c) is uploaded with on a
 * real Neo-Geo: palette words go to palette RAM, and fix-layer tile-map entries
 * go through the VRAM address/data/increment registers. It is written in the
 * pointer + array + volatile-MMIO style that the m68k back end gained in this
 * stage, and it compiles with `shivyc --target m68k`.
 *
 * It is NOT run here: there is no Neo-Geo BIOS / GnGeo in this environment, and
 * these are bare hardware addresses. Compilation to valid m68k is the milestone;
 * a runnable ROM additionally needs ngdevkit packaging (vectors, header, link).
 */

/* Neo-Geo memory-mapped hardware registers (LSPC2 + palette RAM). */
#define PALETTE_RAM   ((volatile unsigned short*)0x400000)
#define REG_VRAMADDR  ((volatile unsigned short*)0x3C0000)
#define REG_VRAMRW    ((volatile unsigned short*)0x3C0002)
#define REG_VRAMMOD   ((volatile unsigned short*)0x3C0004)

/* Fix-layer tile map lives at VRAM word address 0x7000. */
#define FIX_MAP_BASE  0x7000

/* Copy `n` palette words into palette RAM starting at `first`. */
void ng_upload_palette(const unsigned short* pal, int n, int first)
{
    int i = 0;
    while (i < n) {
        PALETTE_RAM[first + i] = pal[i];
        i = i + 1;
    }
}

/* Write `count` fix-map entries (tile index + palette in the high nibble) for a
 * run starting at column/row (cx,cy). The fix map is column-major: stepping the
 * VRAM auto-increment by 1 moves down a column, so a horizontal run sets the
 * address per cell. */
void ng_upload_fix_run(const unsigned short* entries, int count, int cx, int cy)
{
    int i = 0;
    *REG_VRAMMOD = 32;                 /* one column step = 32 rows */
    while (i < count) {
        *REG_VRAMADDR = FIX_MAP_BASE + (cx + i) * 32 + cy;
        *REG_VRAMRW = entries[i];
        i = i + 1;
    }
}

/* Build a fix-map entry: tile number in the low 12 bits, palette in the high 4. */
unsigned short ng_fix_entry(int tile, int palette)
{
    return (unsigned short)((palette << 12) | (tile & 0x0FFF));
}

/* A whole loading-screen upload: palette, then a row of fix tiles. On hardware
 * `main` would be the ROM entry after BIOS init. */
unsigned short title_pal[4] = { 0x0000, 0x7FFF, 0x30FF, 0x4F00 };

int main(void)
{
    unsigned short row[8];
    int i = 0;

    ng_upload_palette(title_pal, 4, 0);

    while (i < 8) {
        row[i] = ng_fix_entry(i + 1, 0);
        i = i + 1;
    }
    ng_upload_fix_run(row, 8, 2, 4);
    return 0;
}
