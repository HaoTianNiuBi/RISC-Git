/* RV-Doctor 裸机链接脚本（M1 实测选定，04 文档 5.2 节"自带完整 link.ld"分支）。
 *
 * 目标机：qemu-system-riscv64/32 -M virt（DRAM 默认 128 MiB，起始 0x80000000）。
 * 不用 -Ttext=0x80000000 的原因（M1 实测）：
 * 1) 它只移动 .text，.rodata/.data 留在默认低地址，PC 相对寻址超 ±2GiB 报
 *    R_RISCV_HI20 截断；
 * 2) RV64 默认 medlow 代码模型用 LUI 绝对寻址，够不到 0x80000000 以上的
 *    符号，必须 -mcmodel=medany。
 * 本脚本把全部段放进 DRAM，并提供 crt0 需要的 gp 窗口、bss 边界与栈顶符号。
 */
ENTRY(_start)

MEMORY
{
    RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 128M
}

SECTIONS
{
    .text : {
        KEEP(*(.text.init))
        *(.text .text.*)
    } > RAM

    .rodata : {
        . = ALIGN(16);
        *(.rodata .rodata.*)
    } > RAM

    .data : {
        . = ALIGN(16);
        __global_pointer$ = . + 0x800;   /* gp ±2KiB 窗口覆盖小数据段 */
        *(.srodata .srodata.*)
        *(.sdata .sdata.*)
        *(.data .data.*)
    } > RAM

    .bss (NOLOAD) : {
        . = ALIGN(16);
        __bss_start = .;
        *(.sbss .sbss.*)
        *(.bss .bss.*)
        *(COMMON)
        . = ALIGN(16);
        __bss_end = .;
    } > RAM

    .preinit_array : {
        __preinit_array_start = .;
        KEEP(*(.preinit_array))
        __preinit_array_end = .;
    } > RAM

    .init_array : {
        __init_array_start = .;
        KEEP(*(SORT_BY_INIT_PRIORITY(.init_array.*)))
        KEEP(*(.init_array))
        __init_array_end = .;
    } > RAM

    .fini_array : {
        __fini_array_start = .;
        KEEP(*(SORT_BY_INIT_PRIORITY(.fini_array.*)))
        KEEP(*(.fini_array))
        __fini_array_end = .;
    } > RAM

    /* 堆必须位于所有初始化数组之后，避免 constructor 中的 malloc 覆盖
     * 尚未执行的 .preinit_array/.init_array 项。libsemihost 的 _sbrk 以
     * _end 为堆起点。 */
    . = ALIGN(16);
    _end = .;
    PROVIDE(end = .);
    PROVIDE(__heap_start = .);

    . = ALIGN(16);
    __rvd_stack_top = ORIGIN(RAM) + LENGTH(RAM);   /* 栈从 DRAM 顶端向下生长 */

    /DISCARD/ : {
        *(.eh_frame .eh_frame.*)
        *(.note .note.*)
        *(.comment)
    }
}
