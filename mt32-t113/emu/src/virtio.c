/* virtio.c - virtio-mmio transport, and split virtqueues.
 *
 * WHY THIS IS HERE. `-M virt` has no I2S, and the T113's I2S-plus-DMAC path is
 * the one driver nobody has written and the one thing that cannot be tested
 * before silicon. A virtio-sound stream is not that driver -- but it is the
 * same *shape*: a descriptor ring the CPU fills, a device that consumes
 * entries at the audio rate on its own clock, period-sized buffers, and a
 * completion notification that tells the producer a buffer is free again.
 * That is exactly the T113's DMAC with a linked descriptor list and a
 * per-descriptor completion interrupt (port/DESIGN.md section 2.3), so
 * rehearsing the ring discipline against a device model that will punish
 * mistakes is worth more than rehearsing it against nothing.
 *
 * Deliberately small: one transport, split rings only, no indirect
 * descriptors, no event suppression, no PCI. `-M virt` exposes 32 virtio-mmio
 * transports at 0x0a000000 on a 0x200 stride with SPI 16..47 -- we scan them
 * for the device ID we want rather than reading the DTB, which means this code
 * does not depend on which slot QEMU happened to use.
 *
 * All queue memory comes from dma_alloc(), i.e. the Normal Non-cacheable
 * window. Under QEMU that is cosmetic (TCG models no caches); on a real
 * machine it is the difference between working and not, and writing it the
 * right way here is the point of the exercise.
 *
 * SPDX-License-Identifier: 0BSD
 */

#include <stdint.h>
#include <stddef.h>
#include "emu.h"
#include "virtio.h"

void *dma_alloc(size_t n, size_t align);
int printf(const char *fmt, ...);
void *memset(void *d, int c, size_t n);

#define VIRTIO_MMIO_BASE    0x0a000000u
#define VIRTIO_MMIO_STRIDE  0x200u
#define VIRTIO_MMIO_SLOTS   32u
#define VIRTIO_MMIO_SPI     16u     /* -> GIC INTID 32 + 16 + slot */

/* Register offsets, virtio 1.2 section 4.2.2. */
#define R_MAGIC             0x000
#define R_VERSION           0x004
#define R_DEVICE_ID         0x008
#define R_VENDOR_ID         0x00C
#define R_DEVICE_FEATURES   0x010
#define R_DEVICE_FEATURES_SEL 0x014
#define R_DRIVER_FEATURES   0x020
#define R_DRIVER_FEATURES_SEL 0x024
#define R_QUEUE_SEL         0x030
#define R_QUEUE_NUM_MAX     0x034
#define R_QUEUE_NUM         0x038
#define R_QUEUE_READY       0x044
#define R_QUEUE_NOTIFY      0x050
#define R_INTERRUPT_STATUS  0x060
#define R_INTERRUPT_ACK     0x064
#define R_STATUS            0x070
#define R_QUEUE_DESC_LOW    0x080
#define R_QUEUE_DESC_HIGH   0x084
#define R_QUEUE_DRIVER_LOW  0x090
#define R_QUEUE_DRIVER_HIGH 0x094
#define R_QUEUE_DEVICE_LOW  0x0A0
#define R_QUEUE_DEVICE_HIGH 0x0A4
#define R_CONFIG            0x100

#define S_ACKNOWLEDGE       1u
#define S_DRIVER            2u
#define S_DRIVER_OK         4u
#define S_FEATURES_OK       8u
#define S_FAILED            0x80u

#define MAGIC_VIRT          0x74726976u     /* "virt" */

static inline uint32_t rd(const virtio_dev *d, unsigned off)
{ return d->base[off / 4u]; }

static inline void wr(const virtio_dev *d, unsigned off, uint32_t v)
{ d->base[off / 4u] = v; }

uint32_t virtio_config_read32(const virtio_dev *d, unsigned off)
{ return rd(d, R_CONFIG + off); }

uint32_t virtio_irq_status(const virtio_dev *d) { return rd(d, R_INTERRUPT_STATUS); }
void virtio_irq_ack(const virtio_dev *d, uint32_t b) { wr(d, R_INTERRUPT_ACK, b); }

int virtio_mmio_find(uint32_t device_id, virtio_dev *out)
{
    unsigned i;
    for (i = 0; i < VIRTIO_MMIO_SLOTS; i++) {
        volatile uint32_t *base =
            (volatile uint32_t *)(uintptr_t)(VIRTIO_MMIO_BASE + i * VIRTIO_MMIO_STRIDE);
        if (base[R_MAGIC / 4u] != MAGIC_VIRT) continue;
        if (base[R_DEVICE_ID / 4u] != device_id) continue;
        out->base      = base;
        out->slot      = i;
        out->irq       = 32u + VIRTIO_MMIO_SPI + i;
        out->device_id = device_id;
        return 0;
    }
    return -1;
}

int virtio_dev_init(virtio_dev *d, uint64_t want, uint64_t *got)
{
    uint32_t version = rd(d, R_VERSION);
    uint64_t dev_features;
    uint64_t use;

    if (version != 2u) {
        /* QEMU's virtio-mmio transport defaults to the LEGACY interface
         * (version 1), which uses QueuePFN and a guest page size register
         * instead of the split descriptor/driver/device addresses this driver
         * writes. Rather than carry a second queue-setup path with nothing to
         * teach us, ask for the modern transport on the command line. */
        printf("[E] virtio: transport is version %u (legacy).\n"
               "[E] virtio: add -global virtio-mmio.force-legacy=false to the "
               "QEMU command line.\n", version);
        return -1;
    }

    wr(d, R_STATUS, 0u);                        /* reset */
    while (rd(d, R_STATUS) != 0u) { }
    wr(d, R_STATUS, S_ACKNOWLEDGE);
    wr(d, R_STATUS, S_ACKNOWLEDGE | S_DRIVER);

    wr(d, R_DEVICE_FEATURES_SEL, 0u);
    dev_features = rd(d, R_DEVICE_FEATURES);
    wr(d, R_DEVICE_FEATURES_SEL, 1u);
    dev_features |= (uint64_t)rd(d, R_DEVICE_FEATURES) << 32;

    use = dev_features & want;
    /* VIRTIO_F_VERSION_1 (bit 32) is mandatory for a modern transport: a
     * device that does not offer it cannot be driven by this code. */
    if (!(use & (1ull << 32))) {
        printf("[E] virtio: device does not offer VERSION_1 (features 0x%08x%08x)\n",
               (uint32_t)(dev_features >> 32), (uint32_t)dev_features);
        virtio_dev_fail(d);
        return -1;
    }

    wr(d, R_DRIVER_FEATURES_SEL, 0u);
    wr(d, R_DRIVER_FEATURES, (uint32_t)use);
    wr(d, R_DRIVER_FEATURES_SEL, 1u);
    wr(d, R_DRIVER_FEATURES, (uint32_t)(use >> 32));

    wr(d, R_STATUS, S_ACKNOWLEDGE | S_DRIVER | S_FEATURES_OK);
    if (!(rd(d, R_STATUS) & S_FEATURES_OK)) {
        printf("[E] virtio: device rejected our feature set\n");
        virtio_dev_fail(d);
        return -1;
    }
    if (got) *got = use;
    return 0;
}

int virtio_dev_ready(virtio_dev *d)
{
    wr(d, R_STATUS, S_ACKNOWLEDGE | S_DRIVER | S_FEATURES_OK | S_DRIVER_OK);
    return (rd(d, R_STATUS) & S_DRIVER_OK) ? 0 : -1;
}

void virtio_dev_fail(virtio_dev *d) { wr(d, R_STATUS, S_FAILED); }

int virtq_setup(const virtio_dev *d, unsigned index, virtq *q)
{
    uint32_t max;
    unsigned n, i;
    size_t desc_bytes, avail_bytes, used_bytes;

    wr(d, R_QUEUE_SEL, index);
    if (rd(d, R_QUEUE_READY) != 0u) {
        printf("[E] virtio: queue %u already ready\n", index);
        return -1;
    }
    max = rd(d, R_QUEUE_NUM_MAX);
    if (max == 0u) { printf("[E] virtio: queue %u does not exist\n", index); return -1; }

    n = 64u;
    if (n > max) n = max;

    desc_bytes  = (size_t)n * sizeof(struct virtq_desc);
    avail_bytes = 6u + (size_t)n * 2u;
    used_bytes  = 6u + (size_t)n * sizeof(struct virtq_used_elem);

    /* virtio 1.2 section 2.7: descriptor table 16-byte aligned, available ring
     * 2-byte, used ring 4-byte. We over-align to 64 so that each lands in its
     * own cache line on a machine that has them. */
    q->desc  = (struct virtq_desc  *)dma_alloc(desc_bytes, 64u);
    q->avail = (struct virtq_avail *)dma_alloc(avail_bytes + 2u, 64u);
    q->used  = (struct virtq_used  *)dma_alloc(used_bytes + 2u, 64u);
    if (!q->desc || !q->avail || !q->used) {
        printf("[E] virtio: no uncached memory for queue %u\n", index);
        return -1;
    }
    memset(q->desc, 0, desc_bytes);
    memset(q->avail, 0, avail_bytes + 2u);
    memset(q->used, 0, used_bytes + 2u);

    /* Free list: every descriptor chained to the next. */
    for (i = 0; i < n; i++) q->desc[i].next = (uint16_t)(i + 1u);
    q->desc[n - 1u].next = 0xFFFFu;
    q->free_head = 0u;
    q->num_free  = (uint16_t)n;
    q->last_used = 0u;
    q->num       = n;
    q->index     = index;
    q->dev       = d;

    wr(d, R_QUEUE_NUM, n);
    wr(d, R_QUEUE_DESC_LOW,    (uint32_t)(uintptr_t)q->desc);
    wr(d, R_QUEUE_DESC_HIGH,   0u);
    wr(d, R_QUEUE_DRIVER_LOW,  (uint32_t)(uintptr_t)q->avail);
    wr(d, R_QUEUE_DRIVER_HIGH, 0u);
    wr(d, R_QUEUE_DEVICE_LOW,  (uint32_t)(uintptr_t)q->used);
    wr(d, R_QUEUE_DEVICE_HIGH, 0u);
    __asm__ volatile("dsb" ::: "memory");
    wr(d, R_QUEUE_READY, 1u);
    return 0;
}

int virtq_add(virtq *q, const virtio_sg *sg, unsigned n)
{
    uint16_t head, prev = 0xFFFFu, idx;
    unsigned i;

    if (n == 0u || q->num_free < n) return -1;

    head = q->free_head;
    idx = head;
    for (i = 0; i < n; i++) {
        struct virtq_desc *dsc = &q->desc[idx];
        uint16_t next = dsc->next;
        dsc->addr  = (uint64_t)(uintptr_t)sg[i].addr;
        dsc->len   = sg[i].len;
        dsc->flags = (uint16_t)((sg[i].device_writes ? VIRTQ_DESC_F_WRITE : 0u)
                                | (i + 1u < n ? VIRTQ_DESC_F_NEXT : 0u));
        prev = idx;
        idx = next;
    }
    (void)prev;
    q->free_head = idx;
    q->num_free = (uint16_t)(q->num_free - n);

    /* Publish the descriptors before the ring entry, and the ring entry before
     * the index -- the same ordering rule as the audio ring, for the same
     * reason. */
    __asm__ volatile("dmb" ::: "memory");
    q->avail->ring[q->avail->idx % q->num] = head;
    __asm__ volatile("dmb" ::: "memory");
    q->avail->idx = (uint16_t)(q->avail->idx + 1u);
    __asm__ volatile("dmb" ::: "memory");
    return (int)head;
}

void virtq_notify(virtq *q)
{
    __asm__ volatile("dsb" ::: "memory");
    q->dev->base[R_QUEUE_NOTIFY / 4u] = q->index;
}

int virtq_reclaim(virtq *q, uint32_t *len_out)
{
    uint16_t head;
    unsigned n = 0;

    __asm__ volatile("dmb" ::: "memory");
    if (q->last_used == q->used->idx) return -1;

    {
        struct virtq_used_elem *e = &q->used->ring[q->last_used % q->num];
        head = (uint16_t)e->id;
        if (len_out) *len_out = e->len;
    }
    __asm__ volatile("dmb" ::: "memory");
    q->last_used = (uint16_t)(q->last_used + 1u);

    /* Walk the chain back onto the free list. */
    {
        uint16_t i = head, last = head;
        for (;;) {
            n++;
            last = i;
            if (!(q->desc[i].flags & VIRTQ_DESC_F_NEXT)) break;
            i = q->desc[i].next;
        }
        q->desc[last].next = q->free_head;
        q->free_head = head;
        q->num_free = (uint16_t)(q->num_free + n);
    }
    return (int)head;
}

unsigned virtq_outstanding(const virtq *q)
{
    return (unsigned)(uint16_t)(q->avail->idx - q->used->idx);
}
