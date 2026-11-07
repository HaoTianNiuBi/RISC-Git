/* RV-Doctor M0 裸机链接脚本 —— QEMU virt 机器内存映射
 * DRAM 起始 0x80000000（virt 默认 128MB），栈顶放 DRAM 末端。
 * 兼容 newlib crt0 对 __stack / __global_pointer$ / __bss_start 等符号的引用。
 * 注意：代码须用 -mcmodel=medany 编译（medlow 够不到 0x80000000）。 */
OUTPUT_ARCH(riscv)
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
    *(.rodata .rodata.*)
    *(.srodata .srodata.*)
  } > RAM

  .data : {
    __global_pointer$ = . + 0x800;
    *(.data .data.*)
    *(.sdata .sdata.*)
  } > RAM

  .bss (NOLOAD) : {
    __bss_start = .;
    *(.bss .bss.*)
    *(.sbss .sbss.*)
    *(COMMON)
    __bss_end = .;
  } > RAM

  . = ALIGN(16);
  __heap_start = .;
  __stack_top = ORIGIN(RAM) + LENGTH(RAM);
}

PROVIDE(__stack = __stack_top);
PROVIDE(_end = __heap_start);
PROVIDE(__end__ = __heap_start);
