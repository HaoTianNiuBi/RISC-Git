/* b07_fib -- 递归斐波那契
 * 故意保留的可优化点（06 文档 §3）：指数级重复递归，可迭代/记忆化。
 * 预期触发 R3（调用次数与总指令数量级下降）。
 * n=17：fib=1597，调用次数 2*fib(18)-1 = 5167。
 */
#include <stdio.h>

static long calls = 0;

static int fib(int n) {
    calls++;
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}

int main(void) {
    int r = fib(17);
    printf("b07 fib: n=17 fib=%d calls=%ld\n", r, calls);
    return 0;
}
