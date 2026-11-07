/* b04_string_search -- 朴素字符串匹配
 * 故意保留的可优化点（06 文档 §3）：内层每轮重算边界、逐字符经函数调用
 * 比较（ch_eq 在 -O0 不内联）。预期触发 R1/R3。
 * 输入固定：LCG(seed=777) 4 字母表文本 T=1200，模式 P=10，固定三处嵌入。
 */
#include <stdio.h>
#include <stdint.h>

#define T 1200
#define P 10
static char text[T + 1];
static char pat[P + 1];

static int ch_eq(char a, char b) { return a == b; } /* 逐字符函数调用比较 */

int main(void) {
    uint32_t x = 777u;
    for (int i = 0; i < T; i++) {
        x = x * 1103515245u + 12345u;
        text[i] = (char)('a' + (char)((x >> 16) % 4u));
    }
    for (int i = 0; i < P; i++) {
        x = x * 1103515245u + 12345u;
        pat[i] = (char)('a' + (char)((x >> 16) % 4u));
    }
    for (int i = 0; i < P; i++) {
        text[100 + i] = pat[i];
        text[500 + i] = pat[i];
        text[900 + i] = pat[i];
    }
    int matches = 0, last = -1;
    for (int i = 0; i + P <= T; i++) {
        int ok = 1;
        for (int j = 0; j < P; j++) {
            if (!ch_eq(text[i + j], pat[j])) { ok = 0; break; }
        }
        if (ok) { matches++; last = i; }
    }
    printf("b04 string_search: T=%d P=%d matches=%d last=%d\n", T, P, matches, last);
    return 0;
}
