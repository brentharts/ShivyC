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

    return 0;
}
