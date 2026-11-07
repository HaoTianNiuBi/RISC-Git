/* b06_grayscale -- 浮点加权灰度（R9 演示重点）
 * 故意保留的可优化点（06 文档 §3）：float 权重 + 乘法，可改整数移位近似。
 * RV64I（无 F/D 扩展）下 float 运算退化为软浮点库调用（__mulsf3 等），
 * 指令数骤降空间大；RV64GC 下为硬件浮点。预期触发 R9。
 * 输入固定：LCG(seed=987654321) 256 像素。累计值取 (int)(gray*4) 避免浮点
 * 格式化差异，golden 跨架构稳定（float32 语义在各 ISA 一致）。
 */
#include <stdio.h>
#include <stdint.h>

#define N 256
static uint8_t R[N], G[N], B[N];

int main(void) {
    uint32_t x = 987654321u;
    for (int i = 0; i < N; i++) {
        x = x * 1103515245u + 12345u;
        R[i] = (uint8_t)(x >> 24);
        G[i] = (uint8_t)(x >> 16);
        B[i] = (uint8_t)(x >> 8);
    }
    int32_t acc = 0;
    for (int i = 0; i < N; i++) {
        float gray = 0.299f * (float)R[i] + 0.587f * (float)G[i]
                   + 0.114f * (float)B[i];
        acc += (int32_t)(gray * 4.0f);
    }
    printf("b06 grayscale: N=%d acc=%d\n", N, acc);
    return 0;
}
