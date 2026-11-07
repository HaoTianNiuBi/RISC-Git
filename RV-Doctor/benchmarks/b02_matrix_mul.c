/* b02_matrix_mul -- N×N 矩阵乘法
 * 故意保留的可优化点（06 文档 §3）：内层 a[i][k]/b[k][j] 乘法寻址重复计算、
 * k 循环内重复取 b 行；可提指针缓存行首。预期触发 R1/R2/R7。
 * 输入固定：LCG(seed=1) 两个矩阵，元素 mod 100。
 */
#include <stdio.h>
#include <stdint.h>

#define N 16
static int32_t A[N][N], B[N][N], C[N][N];

int main(void) {
    uint32_t x = 1u;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            x = x * 1103515245u + 12345u;
            A[i][j] = (int32_t)((x >> 16) % 100u);
        }
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            x = x * 1103515245u + 12345u;
            B[i][j] = (int32_t)((x >> 16) % 100u);
        }
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            int32_t s = 0;
            for (int k = 0; k < N; k++)
                s += A[i][k] * B[k][j];
            C[i][j] = s;
        }
    int32_t chk = 0;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
            chk += C[i][j];
    printf("b02 matrix_mul: N=%d checksum=%d\n", N, chk);
    return 0;
}
