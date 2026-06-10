/* Tokenize multi-line samples with the transpiled lexer. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#include "errors_core.h"
#include "lexer_core.h"
#include "token_kinds.h"
#include "shivycx_exceptions.h"

static const char *kind_label(TokenKind *kind) {
    if (kind == identifier) return "identifier";
    if (kind == number) return "number";
    if (kind == string) return "string";
    if (kind == char_string) return "char_string";
    if (kind == include_file) return "include_file";
    if (kind == unrecognized) return "unrecognized";
    if (kind->text_repr && kind->text_repr[0]) return kind->text_repr;
    return "?";
}

static const char *token_text(Token *tok) {
    if (tok->rep && tok->rep[0]) return tok->rep;
    if (tok->content) return tok->content;
    return "";
}

static void print_result(const char *code) {
    ErrorCollector_clear(error_collector);
    shivycx_clear_pending_error();

    TokenList *tokens = tokenize(code, "harness.c");
    if (!tokens) {
        printf("error:tokenize returned NULL\n");
        return;
    }

    printf("tokens:%zu\n", TokenList_len(tokens));
    for (size_t i = 0; i < TokenList_len(tokens); i++) {
        Token *tok = TokenList_get(tokens, i);
        printf("token:%s:%s:L%d\n", kind_label(tok->kind), token_text(tok), tok->logical_line);
    }
    if (!ErrorCollector_ok(error_collector)) {
        printf("issues:%d\n", error_collector->issue_count);
    }
}

int main(int argc, char **argv) {
    init_errors_core();
    init_token_kinds();

    if (argc > 1) {
        print_result(argv[1]);
        return 0;
    }

    print_result("int x = 42;\n");
    return 0;
}
