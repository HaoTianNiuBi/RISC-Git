/* b03_crc32 -- 逐位循环 CRC-32
 * 故意保留的可优化点（06 文档 §3）：每位一分支，可改查表法。
 * 预期触发 R6（branch 大幅下降，总指令下降）。
 * 输入固定：LCG(seed=424242) 96 字节。多项式 0xEDB88320（zlib 约定）。
 */
#include <stdio.h>
#include <stdint.h>

#define LEN 96
static uint8_t buf[LEN];

int main(void) {
    uint32_t x = 424242u;
    for (int i = 0; i < LEN; i++) {
        x = x * 1103515245u + 12345u;
        buf[i] = (uint8_t)(x >> 24);
    }
    uint32_t crc = 0xFFFFFFFFu;
    for (int i = 0; i < LEN; i++) {
        crc ^= buf[i];
        for (int b = 0; b < 8; b++) {
            uint32_t mask = (crc & 1u) ? 0xEDB88320u : 0u;
            crc = (crc >> 1) ^ mask;
        }
    }
    crc ^= 0xFFFFFFFFu;
    printf("b03 crc32: len=%d crc=%08x\n", LEN, crc);
    return 0;
}
