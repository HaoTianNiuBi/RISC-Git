/* RV-Doctor 最小裸机启动代码（M1，随 riscv_compile 一起参与链接）。
 *
 * 为什么不用 newlib 的 crt0（M1 实测结论，见 docs/04-环境搭建指南.md 第 9 节；
 * 早期实验源码归档于 docs/archive/runtime-experiments/）：
 * newlib crt0 假定引导固件已设好 sp——其 _start 里直接 lw a0,0(sp) 读 argc。
 * qemu-system-riscv64 -bios none 复位后 sp=0，第一条栈操作即 store fault，
 * 而 mtvec 也是 0，于是掉进取指 fault 死循环。因此这里自设 gp/sp、清 bss、
 * 执行 init_array，再调用 main，并把返回值交给 newlib 的 exit（半主机
 * SYS_EXIT 正常退出）。printf 等仍走 --specs=semihost.specs 提供的
 * libsemihost 系统调用层。
 *
 * 32/64 位通用：访存宽度按 __riscv_xlen 由 cpp 选择。
 */
    .section .text.init, "ax", @progbits
    .globl _start
_start:
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop
    la      sp, __rvd_stack_top

    /* 关闭 CLINT 定时器中断（M0 实测结论：mtimecmp 复位为 0 时 MTIP 常挂，
     * 与 QEMU TB 链产生时序竞态，导致 trace 执行计数抖动）。写最大值消噪。
     * rv32 的 mtimecmp 拆成两个 32 位字，先写低字防瞬时中断。 */
#if __riscv_xlen == 64
    li      t0, -1
    li      t1, 0x02004000
    sd      t0, 0(t1)
#else
    li      t1, 0x02004000
    li      t0, -1
    sw      t0, 0(t1)           /* 低字 = 0xffffffff（先写，防瞬时比较命中） */
    sw      t0, 4(t1)           /* 高字 = 0xffffffff → mtimecmp = 最大值 */
#endif

    /* 使能浮点单元：mstatus.FS 复位后为 Off(00)，任何 FP 指令触发
     * illegal-instruction（M3 实测：b06 首条 fcvt 即挂死）。置 FS=Initial(01)
     * 后首次 FP 运算由硬件转入 Dirty。仅在目标含 F 扩展时执行：
     * rv32i/rv64i 的 -march 不含 zicsr，csr* 无法汇编（矩阵测试实测），
     * 且整数目标上该写毫无意义。.option arch +zicsr 兜底保证汇编通过。 */
#if __riscv_flen > 0
    li      t0, 0x2000
    .option push
    .option arch, +zicsr
    csrs    mstatus, t0
    .option pop
#endif

    /* 清零 .bss（__bss_start/__bss_end 16 字节对齐，见 link.ld） */
    la      t0, __bss_start
    la      t1, __bss_end
#if __riscv_xlen == 64
1:  bgeu    t0, t1, 2f
    sd      zero, 0(t0)
    addi    t0, t0, 8
    j       1b
#else
1:  bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
#endif
2:
    li      a0, 0               /* main(void)；a1/a2 置 0 防误用 */
    li      a1, 0
    li      a2, 0
    call    __libc_init_array
    call    main
    call    exit                /* 不返回：flush stdio 后 _exit → SYS_EXIT */
3:  j       3b
