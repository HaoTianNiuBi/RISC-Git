/* R9 正例探针：rv64i（无 F 扩展）下 float 运算退化为 libgcc 软浮点调用。
 * 预期规则引擎输出 R9（__mulsf3 等），且给出真实调用点与动态计数。 */
#include <stdio.h>

int main(void) {
    float w0 = 0.299f, w1 = 0.587f, w2 = 0.114f;
    unsigned char px[8] = {10, 20, 30, 40, 50, 60, 70, 80};
    float acc = 0.0f;
    for (int i = 0; i < 8; i += 3) {
        acc += w0 * px[i] + w1 * px[i + 1] + w2 * px[i + 2];
    }
    printf("gray=%d\n", (int)acc);
    return 0;
}
