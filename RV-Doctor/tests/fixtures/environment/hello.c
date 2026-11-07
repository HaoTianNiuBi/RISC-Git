#include <stdio.h>

int main(void) {
    int sum = 0;
    for (int i = 1; i <= 100; i++) {
        sum += i;
    }
    printf("sum=%d\n", sum);   /* 预期输出 sum=5050 */
    return 0;
}
