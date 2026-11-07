/* RV-Doctor M0 最小裸机运行时（配合 runtime.S 与 link.ld 使用）
 *
 * 背景：xPack GCC 15.2.0（newlib 4.x）的 semihost.specs 在 QEMU 11.1 下
 * 无法正常触发 semihosting 调用（ebreak 落入异常循环）。按 08 文档 T3
 * 预案改为自研最小运行时：只依赖 _start/_write/_sbrk/_exit 四个入口，
 * 直接实现 RISC-V 半主机魔法指令序列，行为完全确定。
 *
 * 编译方式：-nostartfiles -T link.ld runtime.S runtime.c <源文件>
 */

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

/* RISC-V 32/64 位通用半主机调用：a0=操作码，a1=参数 */
static long semihost_call(long op, void *arg)
{
    register long a0 asm("a0") = op;
    register long a1 asm("a1") = (long)arg;
    asm volatile (
        ".balign 16\n"
        ".option push\n"
        ".option norvc\n"
        "slli zero, zero, 0x1f\n"
        "ebreak\n"
        "srai zero, zero, 7\n"
        ".option pop\n"
        : "+r"(a0)
        : "r"(a1)
        : "memory");
    return a0;
}

#define SEMI_SYS_WRITE0 0x04
#define SEMI_SYS_EXIT   0x18

extern char __heap_start[];
extern char __stack_top[];

void _exit(int code) __attribute__((noreturn, used));

void _exit(int code)
{
#if __riscv_xlen == 64
    /* 64 位 SYS_EXIT：a1 指向 {reason, subcode} 双字段块 */
    static volatile uint64_t exit_block[2];
    exit_block[0] = 0x20026;            /* ADP_Stopped_ApplicationExit */
    exit_block[1] = (uint64_t)code;     /* QEMU 以 subcode 作为进程退出码 */
    semihost_call(SEMI_SYS_EXIT, (void *)exit_block);
#else
    /* 32 位 SYS_EXIT：a1 直接为 reason 码 */
    semihost_call(SEMI_SYS_EXIT, (void *)(uintptr_t)0x20026);
#endif
    for (;;)
        ;
}

/* _write：将 buf/len 经 SYS_WRITE0 输出（需 NUL 结尾，分块拷贝） */
int _write(int fd, const char *buf, size_t len)
{
    (void)fd;
    static char chunk[1024];
    size_t done = 0;
    while (done < len) {
        size_t n = len - done;
        if (n > sizeof(chunk) - 1)
            n = sizeof(chunk) - 1;
        for (size_t i = 0; i < n; i++)
            chunk[i] = buf[done + i];
        chunk[n] = '\0';
        semihost_call(SEMI_SYS_WRITE0, chunk);
        done += n;
    }
    return (int)len;
}

/* _sbrk：newlib malloc 的内存来源；无对齐增长需求时 bump 即可 */
void *_sbrk(ptrdiff_t increment)
{
    static char *brk = NULL;
    if (brk == NULL)
        brk = __heap_start;
    char *old = brk;
    char *next = old + increment;
    /* 预留 64KB 栈保护区 */
    if (next > __stack_top - 64 * 1024)
        return (void *)-1;
    brk = next;
    return old;
}

/* runtime_init：关时钟中断 + printf 无缓冲，保证执行流确定可复现 */
void runtime_init(void) __attribute__((used));
void runtime_init(void)
{
    /* virt 机器 CLINT：把 mtimecmp 设为最大值，清除常挂起的 MTIP。
     * -bios none 下无人初始化定时器，MTIP 常挂会与 QEMU 的 TB 链产生
     * 宿主机时序竞态，导致指令计数 ±2 抖动（M0 实测）。 */
    *(volatile uint64_t *)0x02004000 = ~(uint64_t)0;
    setvbuf(stdout, NULL, _IONBF, 0);
}

/* 其余 newlib 可能引用的桩：满足链接即可 */
int _close(int fd)           { (void)fd; return -1; }
int _fstat(int fd, void *st) { (void)fd; (void)st; return 0; }
int _isatty(int fd)          { (void)fd; return 1; }
int _lseek(int fd, long off, int whence) { (void)fd; (void)off; (void)whence; return 0; }
int _read(int fd, void *buf, size_t len) { (void)fd; (void)buf; (void)len; return 0; }
void _open(void) {}
void _unlink(void) {}
void _getpid(void) {}
void _kill(void) {}
