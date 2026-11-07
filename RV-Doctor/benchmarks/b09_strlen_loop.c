/* b09_strlen_loop -- 循环内 strlen（R1 招牌用例）
 * 故意保留的可优化点（06 文档 §3）：strlen(s) 是循环不变量却在条件中每次
 * 迭代重新调用（-O0 下不会外提）；外提后循环体内 call 清零，总指令显著下降。
 * 预期触发 R1。
 * 输入固定：LCG(seed=555) 200 字符文本，固定 6 处 'w'（索引 < ITER 均可达）。
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
    for (int i = 0; i < ITER; i++) {
        size_t len = strlen(s); /* 循环不变量：每迭代重算（R1 证据源） */
        if (s[i % (int)len] == 'w')
            count++;
    }
    printf("b09 strlen_loop: LEN=%d ITER=%d count=%d\n", LEN, ITER, count);
    return 0;
}
