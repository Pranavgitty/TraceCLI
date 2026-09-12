int inner(int x) {
    int doubled = x * 2; // breakpoint target
    return doubled;
}

int middle(int x) {
    return inner(x) + 1;
}

int main() {
    int result = middle(21);
    return result == 43 ? 0 : 1;
}
