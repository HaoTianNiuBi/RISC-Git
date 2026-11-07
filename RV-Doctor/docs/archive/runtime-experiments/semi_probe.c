/* semi_probe.c — 隔离实验（v2）：经 compile_source 全链路（rvcrt0 + link.ld）。
 * 不加 .option norvc，让汇编器把 ebreak 压缩成 c.ebreak（-march=rv64gc），
 * 用于验证 QEMU 11.1 对压缩版半主机魔法序列的识别。 */
int main(void) {
    register long a0 __asm__("a0");
    register long a1 __asm__("a1");

    a0 = 0x04;                    /* SYS_WRITE0 */
    a1 = (long) "probe-compressed:ok\n";
    __asm__ volatile (
        "slli zero, zero, 0x1f\n"
        "ebreak\n"
        "srai zero, zero, 7"
        : "+r"(a0) : "r"(a1) : "memory");

    a0 = 0x18;                    /* SYS_EXIT, 64 位下 a1=原因码 */
    a1 = 0x20026;                 /* ADP_Stopped_ApplicationExit */
    __asm__ volatile (
        "slli zero, zero, 0x1f\n"
        "ebreak\n"
        "srai zero, zero, 7"
        : "+r"(a0) : "r"(a1) : "memory");

    for (;;) { }                  /* QEMU 若未退出，卡在这 */
}
