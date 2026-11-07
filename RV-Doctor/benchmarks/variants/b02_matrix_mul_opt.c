/* b02_matrix_mul_opt -- R2/R7 优化变体：循环序调整 i-k-j + 行指针缓存
 * （对应 06 文档 §3 b02：内层乘法寻址重复计算、k 循环内重复取 b 行）
 * 修改：内层从 k 次全地址重算改为按行顺序访问——A[i][k] 提为循环不变量
 * aik，B 行首缓存为 brow，C 行缓存为 crow，内层只做 crow[j] += aik*brow[j]。
 * 正确性：全整数运算，加法次序改变不影响结果（结合律），checksum 与
 * golden 逐字节一致（由 optimizer.verifier 把关）。
 * 预期：内层循环的地址计算指令（乘法寻址、slli/add 链）大幅减少，
 * 访存与整数地址类动态指令下降；i-k-j 顺序对 B 按行访问，对缓存友好
 *（TCG 不模拟缓存，此项不体现在实测指标中，仅作静态说明）。
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
    /* i-k-j：C 行累加。整数加法结合律保证与 i-j-k 结果逐元素相同。 */
    for (int i = 0; i < N; i++) {
        int32_t *crow = C[i];
        for (int j = 0; j < N; j++)
            crow[j] = 0;
        for (int k = 0; k < N; k++) {
            const int32_t aik = A[i][k];
            const int32_t *brow = B[k];
            for (int j = 0; j < N; j++)
                crow[j] += aik * brow[j];
        }
    }
    int32_t chk = 0;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
            chk += C[i][j];
    printf("b02 matrix_mul: N=%d checksum=%d\n", N, chk);
    return 0;
}
