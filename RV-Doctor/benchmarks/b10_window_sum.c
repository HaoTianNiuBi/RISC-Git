/* b10_window_sum -- 滑动窗口求和
 * 故意保留的可优化点（06 文档 §3）：每个窗口从头累加，可增量更新
 * （加进减出）。预期触发 R1/R7（重复加法与访存下降）。
 * 输入固定：LCG(seed=8888) 400 元素 mod 1000，窗口 W=40。
 */
#include <stdio.h>
#include <stdint.h>

#define N 400
#define W 40
static int32_t v[N];

int main(void) {
    uint32_t x = 8888u;
    for (int i = 0; i < N; i++) {
        x = x * 1103515245u + 12345u;
        v[i] = (int32_t)((x >> 16) % 1000u);
    }
    int32_t total = 0, maxw = -1;
    for (int i = 0; i + W <= N; i++) {
        int32_t s = 0;
        for (int j = 0; j < W; j++)
            s += v[i + j]; /* 每窗口从头累加 → 可增量更新 */
        total += s;
        if (s > maxw) maxw = s;
    }
    printf("b10 window_sum: N=%d W=%d total=%d max=%d\n", N, W, total, maxw);
    return 0;
}
