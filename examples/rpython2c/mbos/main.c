/* main.c -- mbos kernel entry.
 *
 * boot64.S hands control here in 64-bit long mode (or boot.S in 32-bit for the
 * text32 build). We bring up the console -- which comes up in a Bochs-VBE
 * graphics framebuffer when one is present, else VGA text mode -- optionally
 * fetch the page over the network, parse it into a DOM, and render it. Output
 * is mirrored to serial so the headless tests can check it. Then we idle.
 *
 * The page text is generated from page.html into page_html.h at build time --
 * the same "HTML is the source, an embedded form is compiled in" split that
 * www2json.py -> page_data.py uses in the Wayland minibrowser.
 */
#include "dom.h"
#include "page_html.h"     /* defines: static const char PAGE_HTML[] = "..."; */

Node *html_parse(const char *src);
void  render_page(Node *doc);
int   net_init(void);
int   net_fetch(const char *path, char *out, int out_max);

#ifdef MBOS_RPYTHON
/* The render path is generated from rpython (dom.py + htmlparse.py + render.py)
 * by py2c; mbos_glue.c bridges it to the console. See rpy/README. */
void mbos_set_page(const char *html);
int  mbos_render_main(void);
#endif

/* Where a page fetched over the network lands (one UDP datagram today). */
static char g_net_page[1600];

void kmain(unsigned int magic, void *mbi) {
    (void)magic; (void)mbi;

    con_init();
#ifdef MBOS_RPYTHON
    ser_puts("\n[mbos] boot ok (rpython render path)\n");
#else
    ser_puts("\n[mbos] boot ok\n");
#endif

    /* If a virtio NIC is present, fetch the page from the host over
     * minikraft-style ARP+UDP; otherwise render the compiled-in page.
     * (Networked boots get /page.html from the server in test_net.py.) */
    const char *page = PAGE_HTML;
    const char *src  = "embedded";
    if (net_init() == 0) {
        int n = net_fetch("/page.html", g_net_page, sizeof(g_net_page));
        if (n > 0) {
            page = g_net_page;
            src  = "network";
        }
    }

    ser_puts("[mbos] page source: ");
    ser_puts(src);
    ser_puts("\n[mbos] ---- page begin ----\n");

#ifdef MBOS_RPYTHON
    mbos_set_page(page);
    mbos_render_main();
#else
    Node *doc = html_parse(page);
    render_page(doc);
#endif

    ser_puts("\n[mbos] ---- page end ----\n");
    ser_puts("[mbos] done.\n");

    for (;;) __asm__ volatile ("hlt");
}
