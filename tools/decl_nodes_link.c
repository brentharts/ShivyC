/* Runtime helpers for polymorphic DeclNode access in transpiled C. */

#include "decl_nodes.h"

DeclNode *decl_node_child(DeclNode *node) {
    if (node == NULL) {
        return NULL;
    }
    if (node->kind == DECL_KIND_POINTER) {
        return ((DeclPointer *)node)->child;
    }
    if (node->kind == DECL_KIND_ARRAY) {
        return ((DeclArray *)node)->child;
    }
    if (node->kind == DECL_KIND_FUNCTION) {
        return ((DeclFunction *)node)->child;
    }
    return NULL;
}

DeclRootList *decl_function_args(DeclNode *node) {
    if (node == NULL || node->kind != DECL_KIND_FUNCTION) {
        return NULL;
    }
    return ((DeclFunction *)node)->args;
}
