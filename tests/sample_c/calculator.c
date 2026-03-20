// 示例被测程序：简单计算器（含若干潜在缺陷，供测试智能体发现）
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// 加法
int add(int a, int b) {
    return a + b;
}

// 减法
int subtract(int a, int b) {
    return a - b;
}

// 乘法
int multiply(int a, int b) {
    return a * b;
}

// 除法（潜在缺陷：未处理除以零）
int divide(int a, int b) {
    return a / b;
}

// 计算数组平均值（潜在缺陷：空数组未处理）
double average(int arr[], int n) {
    int sum = 0;
    for (int i = 0; i < n; i++) {
        sum += arr[i];
    }
    return (double)sum / n;
}

// 字符串反转（潜在缺陷：NULL未处理）
void reverse_string(char *s) {
    int len = strlen(s);
    for (int i = 0; i < len / 2; i++) {
        char tmp = s[i];
        s[i] = s[len - 1 - i];
        s[len - 1 - i] = tmp;
    }
}

// 阶乘（潜在缺陷：负数未处理）
long long factorial(int n) {
    if (n == 0) return 1;
    return n * factorial(n - 1);
}

int main(int argc, char *argv[]) {
    if (argc < 4) {
        printf("Usage: %s <op> <a> <b>\n", argv[0]);
        printf("ops: add, sub, mul, div, fact, avg\n");
        return 1;
    }

    char *op = argv[1];
    int a = atoi(argv[2]);
    int b = atoi(argv[3]);

    if (strcmp(op, "add") == 0) {
        printf("%d\n", add(a, b));
    } else if (strcmp(op, "sub") == 0) {
        printf("%d\n", subtract(a, b));
    } else if (strcmp(op, "mul") == 0) {
        printf("%d\n", multiply(a, b));
    } else if (strcmp(op, "div") == 0) {
        printf("%d\n", divide(a, b));
    } else if (strcmp(op, "fact") == 0) {
        printf("%lld\n", factorial(a));
    } else {
        printf("Unknown op: %s\n", op);
        return 1;
    }

    return 0;
}