/* RV-Doctor M0 最小 _start：设 gp/sp、清 BSS、调 main、以返回值 _exit。
 * 配合 runtime.c / link.ld，-nostartfiles 编译。RV32/RV64 通用。 */
    .option norvc
    .section .text.init
    .global _start
_start:
    # 关键：gp 必须最先初始化，且要用 .option norelax 阻止
    # "la gp" 自身被松弛成 gp 相对寻址（鸡生蛋问题）
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop
    la      sp, __stack_top

    # 清 BSS：__bss_start → __bss_end
    la      t0, __bss_start
    la      t1, __bss_end
1:  bgeu    t0, t1, 2f
#if __riscv_xlen == 64
    sd      zero, 0(t0)
    addi    t0, t0, 8
#else
    sw      zero, 0(t0)
    addi    t0, t0, 4
#endif
    j       1b
2:
    call    runtime_init        # stdio 无缓冲设置（见 runtime.c）
    call    main
    tail    _exit               # _exit(main 返回值)
