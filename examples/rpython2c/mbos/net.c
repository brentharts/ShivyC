/* net.c -- mbos networking: a compact polled virtio-net driver + just enough
 * ARP/IPv4/UDP to fetch a page from the host.
 *
 * This is the freestanding, minimal stand-in for minikraft's network stack
 * (drivers/virtio-net + lib/netdev + the packet-level ARP/IP/UDP the echo
 * server does in app.c). Same concepts, drastically smaller: legacy virtio-PCI
 * only, one RX + one TX queue, fully polled (no interrupts), static buffers
 * (no allocator). The netfetch protocol on top is a single UDP exchange:
 * we send the path, the host's server replies with the HTML.
 *
 * Wire setup this expects (see Makefile / test_net.py):
 *   qemu ... -device virtio-net-pci,netdev=n0,disable-modern=on
 *            -netdev user,id=n0,net=192.168.100.0/24,host=192.168.100.1
 * Guest is static 192.168.100.2; the host alias 192.168.100.1 NATs to
 * 127.0.0.1, where the page server listens (same addressing convention as
 * minikraft's echo demo).
 */
#include "mbos.h"

int net_init(void);                          /* 0 on success               */
int net_fetch(const char *path, char *out, int out_max);  /* bytes or <0   */

/* ---- byte order --------------------------------------------------------- */
static u16 htons16(u16 v) { return (u16)((v << 8) | (v >> 8)); }
#define ntohs16 htons16
static u32 htonl32(u32 v) {
    return ((v & 0xFF) << 24) | ((v & 0xFF00) << 8) |
           ((v >> 8) & 0xFF00) | (v >> 24);
}

/* ---- 32-bit port I/O (mbos.h has the 8-bit ones) ------------------------ */
static inline void outw(u16 port, u16 val) {
    __asm__ volatile ("outw %0, %1" : : "a"(val), "Nd"(port));
}
static inline u16 inw(u16 port) {
    u16 r; __asm__ volatile ("inw %1, %0" : "=a"(r) : "Nd"(port)); return r;
}
static inline void outl(u16 port, u32 val) {
    __asm__ volatile ("outl %0, %1" : : "a"(val), "Nd"(port));
}
static inline u32 inl(u16 port) {
    u32 r; __asm__ volatile ("inl %1, %0" : "=a"(r) : "Nd"(port)); return r;
}
static inline void barrier(void) { __asm__ volatile ("mfence" ::: "memory"); }

/* ---- PCI config space (mechanism #1) ------------------------------------ */
static u32 pci_read32(u8 bus, u8 dev, u8 fn, u8 off) {
    u32 addr = 0x80000000u | ((u32)bus << 16) | ((u32)dev << 11)
             | ((u32)fn << 8) | (off & 0xFC);
    outl(0xCF8, addr);
    return inl(0xCFC);
}
static void pci_write32(u8 bus, u8 dev, u8 fn, u8 off, u32 val) {
    u32 addr = 0x80000000u | ((u32)bus << 16) | ((u32)dev << 11)
             | ((u32)fn << 8) | (off & 0xFC);
    outl(0xCF8, addr);
    outl(0xCFC, val);
}

/* ---- legacy virtio-PCI register block (I/O BAR0) ------------------------ */
#define VP_HOST_FEATURES  0x00
#define VP_GUEST_FEATURES 0x04
#define VP_QUEUE_PFN      0x08
#define VP_QUEUE_NUM      0x0C
#define VP_QUEUE_SEL      0x0E
#define VP_QUEUE_NOTIFY   0x10
#define VP_STATUS         0x12
#define VP_ISR            0x13
#define VP_CONFIG         0x14   /* device config (MAC) when MSI-X is off */

#define VSTAT_ACK      1
#define VSTAT_DRIVER   2
#define VSTAT_DRIVER_OK 4

/* ---- legacy split virtqueue layout -------------------------------------- */
struct vring_desc  { u32 addr_lo, addr_hi; u32 len; u16 flags; u16 next; };
struct vring_avail { u16 flags; u16 idx; u16 ring[]; };
struct vring_used_elem { u32 id; u32 len; };
struct vring_used  { u16 flags; u16 idx; struct vring_used_elem ring[]; };
#define VRING_DESC_F_NEXT  1
#define VRING_DESC_F_WRITE 2

#define QSZ 256                     /* QEMU's legacy virtio-net queue size  */
/* layout for num=256: desc 4096B @0, avail 4+512B @0x1000,
 * used (4096-aligned) @0x2000, 4+8*256=2052B -> ring fits in 3 pages       */
#define RING_BYTES (3 * 4096)

struct vq {
    u8  *mem;                       /* page-aligned ring memory             */
    struct vring_desc  *desc;
    struct vring_avail *avail;
    struct vring_used  *used;
    u16  qidx;                      /* 0=RX, 1=TX                            */
    u16  last_used;
    u16  next_avail;
};

/* static, page-aligned ring + packet memory (no allocator on bare metal)   */
static u8 g_ring_mem[2 * RING_BYTES] __attribute__((aligned(4096)));

#define VNET_HDR 10                 /* virtio_net_hdr, no MRG_RXBUF          */
#define BUF_BYTES 1792              /* hdr + max ethernet frame, padded      */
#define N_RX 16
static u8 g_rxbuf[N_RX][BUF_BYTES] __attribute__((aligned(16)));
static u8 g_txbuf[BUF_BYTES]       __attribute__((aligned(16)));

static struct vq g_rx, g_tx;
static u16 g_iobase;
static u8  g_mac[6];

/* ---- addressing (minikraft echo-demo convention) ------------------------ */
#define GUEST_IP  ((192u<<24)|(168u<<16)|(100u<<8)|2u)   /* 192.168.100.2   */
#define HOST_IP   ((192u<<24)|(168u<<16)|(100u<<8)|1u)   /* 192.168.100.1   */
#define FETCH_PORT 8080
#define LOCAL_PORT 43210

static u8 g_host_mac[6];
static int g_have_host_mac;

/* ---- virtqueue helpers --------------------------------------------------- */
static void vq_setup(struct vq *q, u16 qidx, u8 *mem) {
    q->mem   = mem;
    q->qidx  = qidx;
    q->desc  = (struct vring_desc *)mem;
    q->avail = (struct vring_avail *)(mem + QSZ * sizeof(struct vring_desc));
    q->used  = (struct vring_used *)(mem + 0x2000);
    q->last_used = 0;
    q->next_avail = 0;
    mini_memset(mem, 0, RING_BYTES);

    outw(g_iobase + VP_QUEUE_SEL, qidx);
    u16 num = inw(g_iobase + VP_QUEUE_NUM);
    if (num != QSZ) {
        /* device queue size differs from our static layout: refuse (the
         * caller reports no-network and mbos falls back to the built-in page) */
        ser_puts("[net] unexpected queue size\n");
    }
    outl(g_iobase + VP_QUEUE_PFN, ((u32)(unsigned long)mem) >> 12);
}

static void vq_notify(struct vq *q) {
    barrier();
    outw(g_iobase + VP_QUEUE_NOTIFY, q->qidx);
}

/* post one buffer; write=1 for device-writable (RX) */
static void vq_post(struct vq *q, void *buf, u32 len, int write) {
    u16 d = q->next_avail % QSZ;
    q->desc[d].addr_lo = (u32)(unsigned long)buf;
    q->desc[d].addr_hi = 0;
    q->desc[d].len     = len;
    q->desc[d].flags   = write ? VRING_DESC_F_WRITE : 0;
    q->desc[d].next    = 0;
    q->avail->ring[q->next_avail % QSZ] = d;
    barrier();
    q->next_avail++;
    q->avail->idx = q->next_avail;
}

/* poll for a used buffer; returns desc id or -1 after `spins` iterations */
static int vq_poll_used(struct vq *q, u32 *len_out, u32 spins) {
    while (spins--) {
        barrier();
        if (q->used->idx != q->last_used) {
            struct vring_used_elem *e = &q->used->ring[q->last_used % QSZ];
            q->last_used++;
            if (len_out) *len_out = e->len;
            return (int)e->id;
        }
        __asm__ volatile ("pause");
    }
    return -1;
}

/* ---- driver bring-up ----------------------------------------------------- */
int net_init(void) {
    /* find virtio-net: vendor 0x1AF4, device 0x1000 (transitional) */
    int bus, dev;
    u32 id = 0; int found = 0; u8 fbus = 0, fdev = 0;
    for (bus = 0; bus < 2 && !found; bus++) {
        for (dev = 0; dev < 32 && !found; dev++) {
            id = pci_read32((u8)bus, (u8)dev, 0, 0x00);
            if ((id & 0xFFFF) == 0x1AF4 && (id >> 16) == 0x1000) {
                fbus = (u8)bus; fdev = (u8)dev; found = 1;
            }
        }
    }
    if (!found) { ser_puts("[net] no virtio-net device\n"); return -1; }

    /* enable I/O + bus mastering, read the I/O BAR */
    u32 cmd = pci_read32(fbus, fdev, 0, 0x04);
    pci_write32(fbus, fdev, 0, 0x04, cmd | 0x5);
    u32 bar0 = pci_read32(fbus, fdev, 0, 0x10);
    if (!(bar0 & 1)) { ser_puts("[net] BAR0 not I/O\n"); return -1; }
    g_iobase = (u16)(bar0 & ~3u);

    /* reset; ACK; DRIVER; features: take none (no MRG_RXBUF -> 10-byte hdr) */
    outb(g_iobase + VP_STATUS, 0);
    outb(g_iobase + VP_STATUS, VSTAT_ACK);
    outb(g_iobase + VP_STATUS, VSTAT_ACK | VSTAT_DRIVER);
    (void)inl(g_iobase + VP_HOST_FEATURES);
    outl(g_iobase + VP_GUEST_FEATURES, 0);

    /* MAC from device config */
    int i;
    for (i = 0; i < 6; i++) g_mac[i] = inb(g_iobase + VP_CONFIG + i);

    /* rings (PFN writes) BEFORE DRIVER_OK, per the legacy spec order */
    vq_setup(&g_rx, 0, g_ring_mem);
    vq_setup(&g_tx, 1, g_ring_mem + RING_BYTES);

    /* pre-post all RX buffers */
    for (i = 0; i < N_RX; i++) vq_post(&g_rx, g_rxbuf[i], BUF_BYTES, 1);

    outb(g_iobase + VP_STATUS, VSTAT_ACK | VSTAT_DRIVER | VSTAT_DRIVER_OK);
    vq_notify(&g_rx);

    ser_puts("[net] virtio-net up, mac ");
    for (i = 0; i < 6; i++) {
        static const char hx[] = "0123456789abcdef";
        char b[4]; b[0] = hx[g_mac[i] >> 4]; b[1] = hx[g_mac[i] & 15];
        b[2] = (i < 5) ? ':' : '\n'; b[3] = 0;
        ser_puts(b);
    }
    return 0;
}

/* ---- frame TX (prepends the 10-byte virtio-net header) ------------------- */
static void net_send(const u8 *frame, u32 len) {
    mini_memset(g_txbuf, 0, VNET_HDR);
    mini_memcpy(g_txbuf + VNET_HDR, frame, len);
    vq_post(&g_tx, g_txbuf, VNET_HDR + len, 0);
    vq_notify(&g_tx);
    (void)vq_poll_used(&g_tx, 0, 2000000);   /* reclaim */
}

/* ---- frame RX; returns payload ptr past the virtio hdr, len, or 0 -------- */
static u8 *net_recv(u32 *len_out, u32 spins) {
    u32 ulen = 0;
    int id = vq_poll_used(&g_rx, &ulen, spins);
    if (id < 0) return 0;
    u8 *pkt = g_rxbuf[id % N_RX];      /* desc id maps 1:1 to buffer index   */
    *len_out = (ulen > VNET_HDR) ? ulen - VNET_HDR : 0;
    /* repost the buffer for future frames */
    vq_post(&g_rx, g_rxbuf[id % N_RX], BUF_BYTES, 1);
    vq_notify(&g_rx);
    return pkt + VNET_HDR;
}

/* ---- protocols: headers --------------------------------------------------*/
struct eth  { u8 dst[6], src[6]; u16 type; } __attribute__((packed));
struct arp  { u16 htype, ptype; u8 hlen, plen; u16 op;
              u8 sha[6]; u32 spa; u8 tha[6]; u32 tpa; } __attribute__((packed));
struct ipv4 { u8 vihl, tos; u16 tlen, id, frag; u8 ttl, proto; u16 csum;
              u32 src, dst; } __attribute__((packed));
struct udp  { u16 sport, dport, len, csum; } __attribute__((packed));

#define ETH_ARP 0x0806
#define ETH_IP  0x0800

static u16 ip_checksum(const void *data, int len) {
    const u8 *p = (const u8 *)data;
    u32 sum = 0;
    while (len > 1) { sum += ((u32)p[0] << 8) | p[1]; p += 2; len -= 2; }
    if (len) sum += (u32)p[0] << 8;
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return htons16((u16)~sum);
}

/* answer "who has GUEST_IP" so slirp can deliver to us */
static void arp_maybe_reply(u8 *pl, u32 len) {
    if (len < sizeof(struct eth) + sizeof(struct arp)) return;
    struct eth *e = (struct eth *)pl;
    struct arp *a = (struct arp *)(pl + sizeof(struct eth));
    if (e->type != htons16(ETH_ARP)) return;
    if (a->op != htons16(1)) return;                    /* request */
    if (a->tpa != htonl32(GUEST_IP)) return;
    u8 out[sizeof(struct eth) + sizeof(struct arp)];
    struct eth *oe = (struct eth *)out;
    struct arp *oa = (struct arp *)(out + sizeof(struct eth));
    mini_memcpy(oe->dst, e->src, 6);
    mini_memcpy(oe->src, g_mac, 6);
    oe->type = htons16(ETH_ARP);
    oa->htype = htons16(1); oa->ptype = htons16(ETH_IP);
    oa->hlen = 6; oa->plen = 4; oa->op = htons16(2);    /* reply */
    mini_memcpy(oa->sha, g_mac, 6);  oa->spa = htonl32(GUEST_IP);
    mini_memcpy(oa->tha, a->sha, 6); oa->tpa = a->spa;
    net_send(out, sizeof(out));
}

/* resolve the host/gateway MAC via ARP (slirp answers for 192.168.100.1) */
static int arp_resolve_host(void) {
    if (g_have_host_mac) return 0;
    u8 out[sizeof(struct eth) + sizeof(struct arp)];
    struct eth *e = (struct eth *)out;
    struct arp *a = (struct arp *)(out + sizeof(struct eth));
    mini_memset(e->dst, 0xFF, 6);
    mini_memcpy(e->src, g_mac, 6);
    e->type = htons16(ETH_ARP);
    a->htype = htons16(1); a->ptype = htons16(ETH_IP);
    a->hlen = 6; a->plen = 4; a->op = htons16(1);       /* request */
    mini_memcpy(a->sha, g_mac, 6); a->spa = htonl32(GUEST_IP);
    mini_memset(a->tha, 0, 6);     a->tpa = htonl32(HOST_IP);

    int tries;
    for (tries = 0; tries < 8; tries++) {
        net_send(out, sizeof(out));
        u32 got = 0;
        u8 *pl = net_recv(&got, 8000000);
        while (pl) {
            struct eth *re = (struct eth *)pl;
            if (got >= sizeof(struct eth) + sizeof(struct arp) &&
                re->type == htons16(ETH_ARP)) {
                struct arp *ra = (struct arp *)(pl + sizeof(struct eth));
                if (ra->op == htons16(2) && ra->spa == htonl32(HOST_IP)) {
                    mini_memcpy(g_host_mac, ra->sha, 6);
                    g_have_host_mac = 1;
                    return 0;
                }
                arp_maybe_reply(pl, got);
            }
            pl = net_recv(&got, 400000);
        }
    }
    return -1;
}

/* one UDP datagram to HOST_IP:FETCH_PORT */
static void udp_send(const char *payload, int plen) {
    u8 out[sizeof(struct eth) + sizeof(struct ipv4) + sizeof(struct udp) + 256];
    struct eth  *e = (struct eth *)out;
    struct ipv4 *ip = (struct ipv4 *)(out + sizeof(struct eth));
    struct udp  *u = (struct udp *)((u8 *)ip + sizeof(struct ipv4));
    u8 *data = (u8 *)u + sizeof(struct udp);
    if (plen > 256) plen = 256;
    mini_memcpy(e->dst, g_host_mac, 6);
    mini_memcpy(e->src, g_mac, 6);
    e->type = htons16(ETH_IP);
    ip->vihl = 0x45; ip->tos = 0;
    ip->tlen = htons16((u16)(sizeof(struct ipv4) + sizeof(struct udp) + plen));
    ip->id = htons16(1); ip->frag = 0; ip->ttl = 64; ip->proto = 17;
    ip->csum = 0; ip->src = htonl32(GUEST_IP); ip->dst = htonl32(HOST_IP);
    ip->csum = ip_checksum(ip, sizeof(struct ipv4));
    u->sport = htons16(LOCAL_PORT); u->dport = htons16(FETCH_PORT);
    u->len = htons16((u16)(sizeof(struct udp) + plen));
    u->csum = 0;                                   /* optional over IPv4 */
    mini_memcpy(data, payload, (size_t)plen);
    net_send(out, sizeof(struct eth) + sizeof(struct ipv4)
                  + sizeof(struct udp) + (u32)plen);
}

/* wait for a UDP datagram to LOCAL_PORT; also answers ARP while waiting */
static int udp_recv(char *out, int out_max, u32 spins_per_poll, int polls) {
    int p;
    for (p = 0; p < polls; p++) {
        u32 got = 0;
        u8 *pl = net_recv(&got, spins_per_poll);
        if (!pl) continue;
        struct eth *e = (struct eth *)pl;
        if (e->type == htons16(ETH_ARP)) { arp_maybe_reply(pl, got); continue; }
        if (e->type != htons16(ETH_IP)) continue;
        if (got < sizeof(struct eth) + sizeof(struct ipv4) + sizeof(struct udp))
            continue;
        struct ipv4 *ip = (struct ipv4 *)(pl + sizeof(struct eth));
        if (ip->proto != 17 || ip->dst != htonl32(GUEST_IP)) continue;
        u32 ihl = (u32)(ip->vihl & 0x0F) * 4;
        struct udp *u = (struct udp *)((u8 *)ip + ihl);
        if (u->dport != htons16(LOCAL_PORT)) continue;
        int plen = (int)ntohs16(u->len) - (int)sizeof(struct udp);
        if (plen < 0) continue;
        if (plen > out_max - 1) plen = out_max - 1;
        mini_memcpy(out, (u8 *)u + sizeof(struct udp), (size_t)plen);
        out[plen] = 0;
        return plen;
    }
    return -1;
}

/* ---- the netfetch protocol: send the path, get the page ------------------ */
int net_fetch(const char *path, char *out, int out_max) {
    if (arp_resolve_host() != 0) {
        ser_puts("[net] ARP: no answer from host\n");
        return -1;
    }
    int attempt;
    for (attempt = 0; attempt < 5; attempt++) {
        udp_send(path, (int)mini_strlen(path));
        int n = udp_recv(out, out_max, 2000000, 40);
        if (n > 0) return n;
    }
    ser_puts("[net] fetch: no reply\n");
    return -1;
}
