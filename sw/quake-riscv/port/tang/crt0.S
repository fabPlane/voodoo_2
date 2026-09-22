        .section .text.start, "ax"
        .align 2
        .globl _start
        .type _start, @function
_start:
        la      sp, _stack_top
        la      t0, _sbss
        la      t1, _ebss
1:
        bgeu    t0, t1, 2f
        sw      zero, 0(t0)
        addi    t0, t0, 4
        j       1b
2:
        call    __libc_init_array
        li      a0, 1
        la      a1, argv_vec
        call    main
1:
        j       1b

        .section .rodata
        .align 2
argv_str:
        .string "quake-sw"
        .align 2
argv_vec:
        .word argv_str
        .word 0
