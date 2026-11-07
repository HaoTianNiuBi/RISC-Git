/* 最小 RISC-V semihosting 通路测试：
 * SYS_WRITE0 输出字符串，SYS_EXIT 结束 QEMU。
 * 若 QEMU 识别魔法序列：打印文字并退出；否则挂死。 */
    .option norvc
    .section .text.init
    .global _start
_start:
    # SYS_WRITE0 (0x04): a0=0x04, a1=字符串指针
    la      a1, msg
    li      a0, 0x04
    slli    zero, zero, 0x1f
    ebreak
    srai    zero, zero, 7

    # SYS_EXIT (0x18): 64 位下 a1 指向 {reason, subcode} 块
    la      a1, exit_block
    li      a0, 0x18
    slli    zero, zero, 0x1f
    ebreak
    srai    zero, zero, 7

1:  j 1b            # 若 semihosting 无效，挂死在这里

    .section .rodata
msg:    .asciz "SEMIHOSTING-OK\n"
    .align 3
exit_block:
    .dword 0x20026      # ADP_Stopped_ApplicationExit
    .dword 0
