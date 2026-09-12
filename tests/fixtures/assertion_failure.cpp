#include <cassert>

int check_positive(int x) {
    assert(x > 0);
    return x;
}

int main() {
    return check_positive(-5);
}
