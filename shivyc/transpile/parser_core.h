#ifndef SHIVYC_PARSER_CORE_H
#define SHIVYC_PARSER_CORE_H

#include "parser_utils.h"
#include "tree_nodes.h"
#include "token_kinds.h"

typedef struct {
    Root *node;
    int index;
} ParseRootResult;

ParseRootResult parse_root(int index);
Root *parse(TokenList *tokens_to_parse);

#endif
