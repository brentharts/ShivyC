/* Exercise transpiled parser_utils against the Python reference. */

#include <stdio.h>
#include <stdbool.h>
#include <string.h>

#include "parser_utils.h"
#include "shivycx_runtime.h"

static void print_bool(const char *label, bool value) {
    printf("%s:%s\n", label, value ? "true" : "false");
}

int main(void) {
    init_parser_utils();
    SimpleSymbolTable *table = symbols;
    SimpleSymbolTable_add_symbol(table, "foo", true);
    SimpleSymbolTable_add_symbol(table, "bar", false);

    print_bool("foo", SimpleSymbolTable_is_typedef(table, "foo"));
    print_bool("bar", SimpleSymbolTable_is_typedef(table, "bar"));
    print_bool("missing", SimpleSymbolTable_is_typedef(table, "missing"));

    SimpleSymbolTable_new_scope(table);
    SimpleSymbolTable_add_symbol(table, "bar", true);
    print_bool("bar_inner", SimpleSymbolTable_is_typedef(table, "bar"));
    print_bool("foo_outer", SimpleSymbolTable_is_typedef(table, "foo"));

    StrBoolMapList *snap = SimpleSymbolTable_snapshot(table);
    SimpleSymbolTable_end_scope(table);
    print_bool("bar_after_pop", SimpleSymbolTable_is_typedef(table, "bar"));

    SimpleSymbolTable_restore(table, snap);
    print_bool("bar_after_restore", SimpleSymbolTable_is_typedef(table, "bar"));
    print_bool("foo_after_restore", SimpleSymbolTable_is_typedef(table, "foo"));

    TokenKind *kind = TokenKind_new(";");
    Position *pos = Position_new("t.c", 1, 1, "int x;");
    Range *rng = Range_new(pos, pos);
    Token *tok = Token_new(kind, "", ";", rng);
    TokenList *toks = (TokenList *)malloc(sizeof(TokenList));
    TokenList_init(toks);
    TokenList_push(toks, tok);
    tokens = toks;
    best_error = NULL;
    clear_pending_parser_error();

    ParserError *err = build_parser_error("expected identifier", 0, PARSER_ERROR_AT);
    printf("err_at:%s\n", err->descrip);
    printf("err_parsed:%d\n", err->amount_parsed);

    TokenList_clear(toks);
    tokens = toks;
    ParserError *err2 = build_parser_error("unexpected token", 0, PARSER_ERROR_GOT);
    printf("err_empty:%s\n", err2->descrip);

    StrBoolMapList *bak = log_error_begin();
    log_error_caught(bak, err);
    printf("best_parsed:%d\n", best_error ? best_error->amount_parsed : -1);

    return 0;
}
