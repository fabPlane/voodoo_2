// SPDX-License-Identifier: GPL-2.0
/*
 * DMA buffer for the Tang Mega 138K Pro dock's frame DMA (FrameDmaWriter):
 * binds to the dock (22c2:1100), allocates a coherent buffer below 4 GiB
 * (the writer uses 3-DW MWr headers) and exposes it as /dev/voodoo_dma.
 * read() returns struct voodoo_dma_info (bus address, size); mmap() maps
 * the buffer. BAR0 is left alone so sysfs resource0 mmap keeps working.
 *
 * /dev/voodoo_bar0 maps BAR0 write-combined (the BAR is non-prefetchable,
 * so sysfs only offers an uncached mapping). Stores then reach the dock as
 * multi-dword write TLPs (B173+ target); the user must fence to keep
 * register order (see sw/pcie/pci_bar.h).
 */
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/miscdevice.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/uaccess.h>

static unsigned int buf_mib = 16;
module_param(buf_mib, uint, 0444);
MODULE_PARM_DESC(buf_mib, "DMA buffer size in MiB");

struct voodoo_dma_info {
	__u64 bus;
	__u64 size;
};

static struct pci_dev *vdev;
static void *cpu_buf;
static dma_addr_t bus_buf;
static size_t buf_size;

static ssize_t vd_read(struct file *f, char __user *ubuf, size_t len, loff_t *off)
{
	struct voodoo_dma_info info = { .bus = bus_buf, .size = buf_size };

	if (len < sizeof(info))
		return -EINVAL;
	if (copy_to_user(ubuf, &info, sizeof(info)))
		return -EFAULT;
	return sizeof(info);
}

static int vd_mmap(struct file *f, struct vm_area_struct *vma)
{
	size_t len = vma->vm_end - vma->vm_start;

	if (vma->vm_pgoff || len > buf_size)
		return -EINVAL;
	return dma_mmap_coherent(&vdev->dev, vma, cpu_buf, bus_buf, len);
}

static const struct file_operations vd_fops = {
	.owner = THIS_MODULE,
	.read = vd_read,
	.mmap = vd_mmap,
};

static struct miscdevice vd_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "voodoo_dma",
	.fops = &vd_fops,
	.mode = 0600,
};

static int vb_mmap(struct file *f, struct vm_area_struct *vma)
{
	size_t len = vma->vm_end - vma->vm_start;
	resource_size_t off = (resource_size_t)vma->vm_pgoff << PAGE_SHIFT;
	resource_size_t start = pci_resource_start(vdev, 0) + off;

	/* A sub-range (offset): Linux PAT gives one memory type per physical
	 * range, so a write-combined map must not overlap the uncached sysfs
	 * resource0 map, or it silently becomes uncached as well. */
	if (off + len > pci_resource_len(vdev, 0))
		return -EINVAL;
	vma->vm_page_prot = pgprot_writecombine(vma->vm_page_prot);
	return io_remap_pfn_range(vma, vma->vm_start, start >> PAGE_SHIFT, len,
				  vma->vm_page_prot);
}

static const struct file_operations vb_fops = {
	.owner = THIS_MODULE,
	.mmap = vb_mmap,
};

static struct miscdevice vb_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "voodoo_bar0",
	.fops = &vb_fops,
	.mode = 0600,
};

static int vd_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
	int ret;

	if (vdev)
		return -EBUSY;
	ret = pci_enable_device(pdev);
	if (ret)
		return ret;
	pci_set_master(pdev);
	ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
	if (ret)
		goto err_disable;
	buf_size = (size_t)buf_mib << 20;
	cpu_buf = dma_alloc_coherent(&pdev->dev, buf_size, &bus_buf, GFP_KERNEL);
	if (!cpu_buf) {
		ret = -ENOMEM;
		goto err_disable;
	}
	memset(cpu_buf, 0, buf_size);
	vdev = pdev;
	ret = misc_register(&vd_misc);
	if (ret)
		goto err_free;
	ret = misc_register(&vb_misc);
	if (ret) {
		misc_deregister(&vd_misc);
		goto err_free;
	}
	dev_info(&pdev->dev, "voodoo_dma: %zu MiB at bus %pad\n", buf_size >> 20, &bus_buf);
	return 0;

err_free:
	dma_free_coherent(&pdev->dev, buf_size, cpu_buf, bus_buf);
	vdev = NULL;
err_disable:
	pci_disable_device(pdev);
	return ret;
}

static void vd_remove(struct pci_dev *pdev)
{
	misc_deregister(&vb_misc);
	misc_deregister(&vd_misc);
	/* The writer may still be enabled: stop bus mastering before freeing. */
	pci_clear_master(pdev);
	dma_free_coherent(&pdev->dev, buf_size, cpu_buf, bus_buf);
	pci_disable_device(pdev);
	vdev = NULL;
}

static const struct pci_device_id vd_ids[] = {
	{ PCI_DEVICE(0x22c2, 0x1100) },
	{ }
};
MODULE_DEVICE_TABLE(pci, vd_ids);

static struct pci_driver vd_driver = {
	.name = "voodoo_dma",
	.id_table = vd_ids,
	.probe = vd_probe,
	.remove = vd_remove,
};
module_pci_driver(vd_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Tang dock SpinalVoodoo frame DMA buffer");
