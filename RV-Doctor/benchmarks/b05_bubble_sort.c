/* b05_bubble_sort -- 冒泡排序
 * 故意保留的可优化点（06 文档 §3）：无提前退出标志；内层边界 N-1-i 每轮
 * 重算。预期触发 R1/R6。
 * 输入固定：LCG(seed=20260101) 120 个元素 mod 10000。
 */
#include <stdio.h>
#include <stdint.h>

#define N 120
static int32_t v[N];

int main(void) {
    uint32_t x = 20260101u;
    for (int i = 0; i < N; i++) {
        x = x * 1103515245u + 12345u;
        v[i] = (int32_t)((x >> 16) % 10000u);
    }
    int32_t swaps = 0;
    for (int i = 0; i < N - 1; i++)
        for (int j = 0; j < N - 1 - i; j++)
            if (v[j] > v[j + 1]) {
                int32_t t = v[j];
                v[j] = v[j + 1];
                v[j + 1] = t;
                swaps++;
            }
    int32_t chk = 0;
    for (int i = 0; i < N; i++)
        chk += v[i] * (int32_t)(i % 7 + 1);
    printf("b05 bubble_sort: N=%d swaps=%d checksum=%d\n", N, swaps, chk);
    return 0;
}
