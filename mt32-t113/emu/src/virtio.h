/* virtio.h - virtio-mmio transport and split virtqueues. See virtio.c.
 * SPDX-License-Identifier: 0BSD */
#ifndef EMU_VIRTIO_H
#define EMU_VIRTIO_H

#include <stdint.h>
#include <stddef.h>

/* Split virtqueue layout, virtio 1.2 section 2.7. Packed because the device
 * reads these structures directly. */
struct virtq_desc {
    uint64_t addr;
    uint32_t len;
    uint16_t flags;
    uint16_t next;
};
#define VIRTQ_DESC_F_NEXT   1u
#define VIRTQ_DESC_F_WRITE  2u

struct virtq_avail {
    uint16_t flags;
    uint16_t idx;
    uint16_t ring[];        /* queue size entries, then used_event */
};

struct virtq_used_elem { uint32_t id; uint32_t len; };

struct virtq_used {
    uint16_t flags;
    uint16_t idx;
    struct virtq_used_elem ring[];
};

typedef struct {
    volatile uint32_t *base;
    unsigned           slot;
    unsigned           irq;     /* GIC INTID */
    uint32_t           device_id;
} virtio_dev;

typedef struct {
    unsigned            index;
    unsigned            num;
    struct virtq_desc  *desc;
    struct virtq_avail *avail;
    struct virtq_used  *used;
    uint16_t            free_head;     /* head of the free-descriptor chain */
    uint16_t            num_free;
    uint16_t            last_used;     /* our cursor into used->ring        */
    const virtio_dev   *dev;
} virtq;

/* A scatter/gather element handed to virtq_add(). */
typedef struct { void *addr; uint32_t len; int device_writes; } virtio_sg;

int  virtio_mmio_find(uint32_t device_id, virtio_dev *out);
int  virtio_dev_init(virtio_dev *d, uint64_t want_features,
                     uint64_t *got_features);
int  virtio_dev_ready(virtio_dev *d);
void virtio_dev_fail(virtio_dev *d);
uint32_t virtio_config_read32(const virtio_dev *d, unsigned off);

int  virtq_setup(const virtio_dev *d, unsigned index, virtq *q);
/* Returns the descriptor head used, or -1 if the queue has no room. */
int  virtq_add(virtq *q, const virtio_sg *sg, unsigned n);
void virtq_notify(virtq *q);
/* Returns the head id of the next completed chain and frees it, or -1. */
int  virtq_reclaim(virtq *q, uint32_t *len_out);
unsigned virtq_outstanding(const virtq *q);

uint32_t virtio_irq_status(const virtio_dev *d);
void     virtio_irq_ack(const virtio_dev *d, uint32_t bits);

#endif
