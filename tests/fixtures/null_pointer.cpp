struct Node {
    int value;
    Node* next;
};

int inner(Node* node) {
    return node->value; // crashes when node is null
}

int main() {
    Node* node = nullptr;
    return inner(node);
}
