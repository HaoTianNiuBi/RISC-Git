/* b09_strlen_loop_opt -- R1 优化变体：循环不变量外提（对应 06 文档 §3 b09）
 * 修改：strlen(s) 是循环不变量，提到循环外只算一次；循环判断逻辑不变。
 * 正确性：s 在循环内不被修改，外提前后 len 值相同，stdout 逐字节一致
 *（由 optimizer.verifier 的 golden 比对与确定性校验把关）。
 * 预期：循环体内 jal strlen 消失，动态调用数 150 → ~1，动态指令总数显著下降。
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#define LEN 200
#define ITER 150
static char s[LEN + 1];

int main(void) {
    uint32_t x = 555u;
    for (int i = 0; i < LEN; i++) {
        x = x * 1103515245u + 12345u;
        s[i] = (char)('a' + (char)((x >> 16) % 26u));
    }
    s[LEN] = '\0';
    static const int wpos[6] = {10, 30, 55, 80, 105, 130};
    for (int k = 0; k < 6; k++)
        s[wpos[k]] = 'w';

    int count = 0;
    size_t len = strlen(s); /* R1 外提：循环不变量只计算一次 */
    for (int i = 0; i < ITER; i++) {
        if (s[i % (int)len] == 'w')
            count++;
    }
    printf("b09 strlen_loop: LEN=%d ITER=%d count=%d\n", LEN, ITER, count);
    return 0;
}
