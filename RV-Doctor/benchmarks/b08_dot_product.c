/* b08_dot_product -- 点积
 * 故意保留的可优化点（06 文档 §3）：每轮重复 load a[i]/b[i]，可 4 路展开。
 * 预期触发 R2/R6（load 与 branch 下降）。
 * 输入固定：LCG(seed=31415926) 两数组 mod 500。
 */
#include <stdio.h>
#include <stdint.h>

#define N 1500
static int32_t a[N], b[N];

int main(void) {
    uint32_t x = 31415926u;
    for (int i = 0; i < N; i++) {
        x = x * 1103515245u + 12345u;
        a[i] = (int32_t)((x >> 16) % 500u);
    }
    for (int i = 0; i < N; i++) {
        x = x * 1103515245u + 12345u;
        b[i] = (int32_t)((x >> 16) % 500u);
    }
    int32_t dot = 0;
    for (int i = 0; i < N; i++)
        dot += a[i] * b[i];
    printf("b08 dot_product: N=%d dot=%d\n", N, dot);
    return 0;
}
