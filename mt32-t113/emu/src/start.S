/* start.S - ARMv7-A reset path for the mt32-t113 bare-metal image.
 *
 * ENTRY CONTRACT. This code assumes exactly the state U-Boot's `bootm` leaves
 * behind, because that is the handover the real board will use:
 *
 *     MMU off, D-cache off and flushed, I-cache state unspecified,
 *     IRQ and FIQ masked, ARM state, PL1, SVC mode, (inferred) secure.
 *
 * That is boot/BRINGUP.md section 4.1's `cleanup_before_linux()` row, and it is
 * also what QEMU's `-kernel` bootloader stub produces on `-M virt`. One image,
 * two loaders. Nothing below depends on any register the loader passes:
 * r0/r1/r2 (0 / machine id / DTB) are recorded for the state probe and then
 * ignored, so an mkimage-wrapped payload and a `-kernel` load behave the same.
 *
 * Order matters and is deliberate:
 *   1. park every core but 0          (the engine is single-threaded)
 *   2. record the entry state         (before we change any of it)
 *   3. CPSR: SVC, interrupts masked, alignment faults off
 *   4. stacks for every mode we can enter
 *   5. VBAR
 *   6. invalidate I-cache, D-cache, TLB, branch predictor
 *   7. BSS
 *   8. VFP/NEON   -- before any C, because C code is built -mfpu=neon-vfpv4
 *                    and GCC will emit VFP register saves in a prologue
 *   9. MMU + caches, if built with MMU=1
 *  10. C++ static constructors, then main()
 *
 * SPDX-License-Identifier: 0BSD
 */

    .syntax unified
    .arm

/* ---------------------------------------------------------------- vectors */
    .section .vectors, "ax"
    .align 5
    .global _vectors
_vectors:
    b   _start                  /* reset                                    */
    b   vec_undef               /* undefined instruction                    */
    b   vec_svc                 /* supervisor call                          */
    b   vec_pabt                /* prefetch abort                           */
    b   vec_dabt                /* data abort                               */
    b   vec_hyp                 /* (hyp trap / reserved)                    */
    b   vec_irq                 /* IRQ                                      */
    b   vec_fiq                 /* FIQ                                      */

/* ------------------------------------------------------------------ reset */
    .section .text.start, "ax"
    .global _start
    .type _start, %function
_start:
    /* 1. Park secondary cores. On the T113 the BROM releases CPU0 only
     *    (boot/BRINGUP.md section 4.3), so this never fires there -- but it is
     *    what makes the image safe if a loader ever starts both, and it is the
     *    "park any second core" the design requires. */
    mrc     p15, 0, r4, c0, c0, 5       /* MPIDR                            */
    ands    r4, r4, #0xFF               /* Aff0 = core number               */
    beq     1f
0:  wfi
    b       0b
1:

    /* 2. Record the entry state before touching it. These live in .data, not
     *    .bss, because BSS has not been cleared yet. main() prints them: this
     *    is the "10-line mrs/mrc dump" boot/BRINGUP.md section 4.4 asks for,
     *    and it is the same code that will answer the secure-vs-non-secure
     *    question on real silicon. */
    ldr     r5, =emu_entry_state
    mrs     r6, cpsr
    str     r6, [r5, #0]
    mrc     p15, 0, r6, c1, c0, 0       /* SCTLR                            */
    str     r6, [r5, #4]
    mrc     p15, 0, r6, c1, c0, 1       /* ACTLR                            */
    str     r6, [r5, #8]
    mrc     p15, 0, r6, c12, c0, 0      /* VBAR                             */
    str     r6, [r5, #12]
    mrc     p15, 0, r6, c0, c0, 0       /* MIDR                             */
    str     r6, [r5, #16]
    mrc     p15, 0, r6, c0, c1, 1       /* ID_PFR1: security/virt extensions */
    str     r6, [r5, #20]
    mrc     p15, 0, r6, c14, c0, 0      /* CNTFRQ                           */
    str     r6, [r5, #24]
    str     r0, [r5, #28]               /* loader r0                        */
    str     r1, [r5, #32]               /* loader r1 (machine id)           */
    str     r2, [r5, #36]               /* loader r2 (DTB / ATAGS)          */
    adr     r6, _start
    str     r6, [r5, #40]               /* where we are actually running    */

    /* 3. Known CPSR: SVC, IRQ+FIQ masked, A (alignment fault) off.
     *    A stays off deliberately: mt32emu's MemParams structures are
     *    __attribute__((packed)) and GCC's byte-wise access for them is fine,
     *    but nothing in this port wants an alignment trap. port/PORTING.md
     *    section 4.10. */
    cpsid   if
    mrs     r0, cpsr
    bic     r0, r0, #0x1F
    orr     r0, r0, #0x13               /* SVC                              */
    msr     cpsr_c, r0
    mrc     p15, 0, r0, c1, c0, 0
    bic     r0, r0, #(1 << 1)           /* SCTLR.A = 0                      */
    bic     r0, r0, #(1 << 13)          /* SCTLR.V = 0: low vectors + VBAR  */
    mcr     p15, 0, r0, c1, c0, 0
    isb

    /* 4. A stack for every mode the CPU can land in. An abort with no ABT
     *    stack is a silent lockup, which is the worst possible first bug. */
    msr     cpsr_c, #(0x11 | 0xC0)      /* FIQ */
    ldr     sp, =__stack_fiq_top
    msr     cpsr_c, #(0x1B | 0xC0)      /* UND */
    ldr     sp, =__stack_und_top
    msr     cpsr_c, #(0x17 | 0xC0)      /* ABT */
    ldr     sp, =__stack_abt_top
    msr     cpsr_c, #(0x12 | 0xC0)      /* IRQ */
    ldr     sp, =__stack_irq_top
    msr     cpsr_c, #(0x13 | 0xC0)      /* SVC */
    ldr     sp, =__stack_svc_top

    /* 5. VBAR. */
    ldr     r0, =_vectors
    mcr     p15, 0, r0, c12, c0, 0
    isb

    /* 6. We arrive with caches off; their contents are undefined, so
     *    invalidate rather than clean. Order: I-cache, BP, TLB, then the
     *    set/way D-cache walk. */
    mov     r0, #0
    mcr     p15, 0, r0, c7, c5, 0       /* ICIALLU                          */
    mcr     p15, 0, r0, c7, c5, 6       /* BPIALL                           */
    mcr     p15, 0, r0, c8, c7, 0       /* TLBIALL                          */
    dsb
    isb
    bl      v7_dcache_invalidate_all

    /* 7a. The TLS block. .tdata is loaded from the image; everything after it
     *     is .tbss and must be zero before libm touches errno. */
    ldr     r0, =__tdata_end
    ldr     r1, =__tls_end
    mov     r2, #0
5:  cmp     r0, r1
    strlo   r2, [r0], #4
    blo     5b

    /* 7. BSS. */
    ldr     r0, =__bss_start
    ldr     r1, =__bss_end
    mov     r2, #0
2:  cmp     r0, r1
    strlo   r2, [r0], #4
    blo     2b

    /* 8. VFP/NEON. Everything in this port is built -mfpu=neon-vfpv4
     *    -mfloat-abi=hard to match bench/ (boot/BRINGUP.md section 6.1), so
     *    the FPU must be live before the first C call, not before the first
     *    float. port/PORTING.md section 4.8 also needs it for the analogue
     *    stage's float low-pass filter even in the integer renderer. */
    mrc     p15, 0, r0, c1, c0, 2       /* CPACR                            */
    orr     r0, r0, #(0xF << 20)        /* cp10, cp11: full access          */
    bic     r0, r0, #(1 << 31)          /* ASEDIS = 0: Advanced SIMD on     */
    bic     r0, r0, #(1 << 30)          /* D32DIS = 0: all 32 D registers   */
    mcr     p15, 0, r0, c1, c0, 2
    isb
    mov     r0, #(1 << 30)              /* FPEXC.EN                         */
    vmsr    fpexc, r0
    mov     r0, #0                      /* FPSCR: RN, no traps, flush-to-
                                         * zero off (mt32emu's integer
                                         * renderer does not need FTZ and we
                                         * would rather match the host) */
    vmsr    fpscr, r0

    /* 9. MMU and caches. See emu/FINDINGS.md for why this is a build knob and
     *    what the two settings mean for the numbers this image prints. */
#if defined(EMU_MMU) && EMU_MMU
    bl      emu_mmu_enable
#endif

    /* 10. C++ static constructors. mt32emu has a few (Tables, ROM tables);
     *     with the fake engine the list is empty and this loop does nothing. */
    ldr     r4, =__init_array_start
    ldr     r5, =__init_array_end
3:  cmp     r4, r5
    bhs     4f
    ldr     r3, [r4], #4
    blx     r3
    b       3b
4:
    mov     r0, #0
    mov     r1, #0
    bl      main
    b       emu_exit_ok

    .size _start, . - _start

/* ------------------------------------------------- D-cache invalidate all */
/* The standard ARMv7 set/way walk, from the ARM ARM B2.2.4 example. Only run
 * at reset, where invalidate (not clean) is what we want: the caches are off
 * and hold whatever the loader left. */
    .global v7_dcache_invalidate_all
    .type v7_dcache_invalidate_all, %function
v7_dcache_invalidate_all:
    push    {r4-r11, lr}
    dmb
    mrc     p15, 1, r0, c0, c0, 1       /* CLIDR                            */
    ands    r3, r0, #0x07000000
    lsr     r3, r3, #23                 /* LoC * 2                          */
    beq     .Lfinished
    mov     r10, #0
.Lloop_level:
    add     r2, r10, r10, lsr #1
    lsr     r1, r0, r2
    and     r1, r1, #7                  /* cache type at this level         */
    cmp     r1, #2
    blt     .Lnext_level
    mcr     p15, 2, r10, c0, c0, 0      /* CSSELR                           */
    isb
    mrc     p15, 1, r1, c0, c0, 0       /* CCSIDR                           */
    and     r2, r1, #7
    add     r2, r2, #4                  /* log2(line bytes)                 */
    ubfx    r4, r1, #3, #10             /* ways - 1                         */
    clz     r5, r4                      /* bit position of way field        */
    ubfx    r7, r1, #13, #15            /* sets - 1                         */
.Lloop_set:
    mov     r9, r4
.Lloop_way:
    lsl     r6, r9, r5
    orr     r11, r10, r6
    lsl     r6, r7, r2
    orr     r11, r11, r6
    mcr     p15, 0, r11, c7, c6, 2      /* DCISW                            */
    subs    r9, r9, #1
    bge     .Lloop_way
    subs    r7, r7, #1
    bge     .Lloop_set
.Lnext_level:
    add     r10, r10, #2
    cmp     r3, r10
    bgt     .Lloop_level
.Lfinished:
    mov     r10, #0
    mcr     p15, 2, r10, c0, c0, 0
    dsb
    isb
    pop     {r4-r11, pc}
    .size v7_dcache_invalidate_all, . - v7_dcache_invalidate_all

/* ----------------------------------------------------------- IRQ dispatch */
    .type vec_irq, %function
vec_irq:
    sub     lr, lr, #4
    srsdb   sp!, #0x13                  /* save LR_irq/SPSR onto the SVC stack */
    cps     #0x13                       /* run the handler in SVC mode       */
    push    {r0-r3, r12, lr}
    and     r1, sp, #4                  /* 8-byte align for the AAPCS        */
    sub     sp, sp, r1
    push    {r1}
    bl      emu_irq_dispatch
    pop     {r1}
    add     sp, sp, r1
    pop     {r0-r3, r12, lr}
    rfeia   sp!
    .size vec_irq, . - vec_irq

/* -------------------------------------------------------- fatal exceptions */
/* Each one lands in C with a number and the faulting PC, prints, and exits.
 * A bare-metal image that spins on a data abort tells you nothing; one that
 * says "data abort at 0x4020xxxx, DFAR 0x0" tells you everything. */
.macro FATAL name, num, adj
    .type \name, %function
\name:
    sub     lr, lr, #\adj
    mov     r1, lr
    mov     r0, #\num
    cps     #0x13
    ldr     sp, =__stack_svc_top
    b       emu_fatal_exception
    .size \name, . - \name
.endm

FATAL vec_undef, 1, 4
FATAL vec_svc,   2, 0
FATAL vec_pabt,  3, 4
FATAL vec_dabt,  4, 8
FATAL vec_hyp,   5, 4
FATAL vec_fiq,   6, 4

/* ---------------------------------------------------------------- helpers */
/* __aeabi_read_tp: the thread pointer.
 *
 * AEABI requires this to preserve r1-r3 and the flags, which is why it is
 * assembly and not C. One thread, one statically allocated TLS block; the
 * 8-byte TCB in front of it is never read by anything, it only fixes the
 * offset the linker bakes into every TPOFF relocation. */
    .global __aeabi_read_tp
    .type __aeabi_read_tp, %function
__aeabi_read_tp:
    ldr     r0, =__tls_start
    sub     r0, r0, #8
    bx      lr
    .size __aeabi_read_tp, . - __aeabi_read_tp

    .global emu_wfi
    .type emu_wfi, %function
emu_wfi:
    dsb
    wfi
    bx      lr
    .size emu_wfi, . - emu_wfi

    .global emu_irq_enable
emu_irq_enable:
    cpsie   i
    bx      lr

    .global emu_irq_disable
emu_irq_disable:
    cpsid   i
    bx      lr

    .section .data
    .align 2
    .global emu_entry_state
emu_entry_state:
    .space 48, 0

    .section .note.GNU-stack,"",%progbits
