#ifndef SHIVYC_TREE_NODES_H
#define SHIVYC_TREE_NODES_H

#include "errors_core.h"
#include "shivycx_runtime.h"

typedef struct Node Node;
struct Node {
    Range *r;
};

DEFINE_LIST(Node, NodeList)

typedef struct Root Root;
struct Root {
    Range *r;
    NodeList *nodes;
};

Node *Node_new(void);
Root *Root_new(NodeList *nodes);

#endif
