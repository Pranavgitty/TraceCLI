int divide(int a, int b) {
    return a / b;
}

int main() {
    volatile int zero = 0;
    return divide(10, zero);
}
