/* Exercise transpiled parser_core against the Python reference. */

#include <stdio.h>
#include <string.h>

#include "parser_core.h"
#include "parser_utils.h"
#include "tokens.h"
#include "token_kinds.h"

static Token *make_tok(TokenKind *kind, const char *spell, int col) {
    Position *pos = Position_new("t.c", 1, col, spell);
    Range *rng = Range_new(pos, pos);
    return Token_new(kind, spell, spell, rng);
}

int main(void) {
    init_parser_utils();
    init_token_kinds();

    TokenList *empty_list = (TokenList *)malloc(sizeof(TokenList));
    TokenList_init(empty_list);
    Root *empty = parse(empty_list);
    printf("empty:%s\n", empty ? "true" : "false");
    if (empty) {
        printf("empty_nodes:%zu\n", NodeList_len(empty->nodes));
    }

    TokenList *semi_tokens = (TokenList *)malloc(sizeof(TokenList));
    TokenList_init(semi_tokens);
    TokenList_push(semi_tokens, make_tok(semicolon, ";", 1));
    TokenList_push(semi_tokens, make_tok(semicolon, ";", 2));
    Root *semi_only = parse(semi_tokens);
    printf("semi_only:%s\n", semi_only ? "true" : "false");
    if (semi_only) {
        printf("semi_nodes:%zu\n", NodeList_len(semi_only->nodes));
    }

    TokenList *bad_tokens = (TokenList *)malloc(sizeof(TokenList));
    TokenList_init(bad_tokens);
    TokenList_push(bad_tokens, make_tok(identifier, "int", 1));
    Root *bad = parse(bad_tokens);
    printf("bad:%s\n", bad ? "false" : "true");
    printf("best:%s\n", best_error ? best_error->descrip : "");

    TokenList *int_x_tokens = (TokenList *)malloc(sizeof(TokenList));
    TokenList_init(int_x_tokens);
    TokenList_push(int_x_tokens, make_tok(int_kw, "int", 1));
    TokenList_push(int_x_tokens, make_tok(identifier, "x", 5));
    TokenList_push(int_x_tokens, make_tok(semicolon, ";", 6));
    Root *int_x = parse(int_x_tokens);
    printf("int_x:%s\n", int_x ? "true" : "false");
    if (int_x) {
        printf("int_x_nodes:%zu\n", NodeList_len(int_x->nodes));
        Declaration *decl = (Declaration *)NodeList_get(int_x->nodes, 0);
        printf("int_x_decls:%zu\n", DeclNodeList_len(decl->node->decls));
    }

    TokenList *proto_tokens = (TokenList *)malloc(sizeof(TokenList));
    TokenList_init(proto_tokens);
    TokenList_push(proto_tokens, make_tok(int_kw, "int", 1));
    TokenList_push(proto_tokens, make_tok(identifier, "f", 5));
    TokenList_push(proto_tokens, make_tok(open_paren, "(", 6));
    TokenList_push(proto_tokens, make_tok(int_kw, "int", 7));
    TokenList_push(proto_tokens, make_tok(close_paren, ")", 10));
    TokenList_push(proto_tokens, make_tok(semicolon, ";", 11));
    Root *proto = parse(proto_tokens);
    printf("proto:%s\n", proto ? "true" : "false");

    TokenList *func_empty_tokens = (TokenList *)malloc(sizeof(TokenList));
    TokenList_init(func_empty_tokens);
    TokenList_push(func_empty_tokens, make_tok(int_kw, "int", 1));
    TokenList_push(func_empty_tokens, make_tok(identifier, "f", 5));
    TokenList_push(func_empty_tokens, make_tok(open_paren, "(", 6));
    TokenList_push(func_empty_tokens, make_tok(close_paren, ")", 7));
    TokenList_push(func_empty_tokens, make_tok(open_brack, "{", 9));
    TokenList_push(func_empty_tokens, make_tok(close_brack, "}", 11));
    Root *func_empty = parse(func_empty_tokens);
    printf("func_empty:%s\n", func_empty ? "true" : "false");
    if (func_empty) {
        Declaration *func_decl = (Declaration *)NodeList_get(func_empty->nodes, 0);
        printf("func_empty_body:%s\n", func_decl->body ? "true" : "false");
        if (func_decl->body) {
            Compound *body = (Compound *)func_decl->body;
            printf("func_empty_items:%zu\n", NodeList_len(body->items));
        }
    }

    return 0;
}
