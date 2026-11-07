/* b01_sum_array -- 数组累加
 * 故意保留的可优化点（06 文档 §3）：下标寻址每次重算地址（a[i] 编译为
 * 基址+偏移重算），可改指针递增 + 2 路展开减分支。预期触发 R6/R7。
 * 输入固定：LCG(seed=20260917)。输出：单行校验和。
 */
#include <stdio.h>
#include <stdint.h>

#define N 2000
static int32_t a[N];

int main(void) {
    uint32_t x = 20260917u;
    for (int i = 0; i < N; i++) {
        x = x * 1103515245u + 12345u;
        a[i] = (int32_t)((x >> 16) & 0xFFFFu);
    }
    int32_t sum = 0;
    for (int i = 0; i < N; i++) {
        sum += a[i];
    }
    printf("b01 sum_array: N=%d sum=%d\n", N, sum);
    return 0;
}
