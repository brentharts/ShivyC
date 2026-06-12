/* Tokenize sample lines with the transpiled lexer and print a stable format. */

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

static void print_result(const char *line, bool in_comment_start) {
    shivycx_clear_pending_error();
    ErrorCollector_clear(error_collector);

    TokenizeLineResult result = tokenize_text_line(line, "harness.c", in_comment_start);

    printf("line:%s\n", line);
    if (shivycx_pending_error) {
        printf("error:%s\n", shivycx_pending_error->descrip);
        return;
    }
    if (!result.tokens) {
        printf("error:tokenize returned NULL\n");
        return;
    }

    for (size_t i = 0; i < TokenList_len(result.tokens); i++) {
        Token *tok = TokenList_get(result.tokens, i);
        printf("token:%s:%s\n", kind_label(tok->kind), token_text(tok));
    }
    printf("in_comment:%s\n", result.in_comment ? "true" : "false");
    if (!ErrorCollector_ok(error_collector)) {
        printf("warnings:%zu\n", ErrorCollector_issue_count(error_collector));
    }
}

static const char *default_samples[] = {
    "int x = 42;",
    "a ? b : c",
    "int x[sizeof(long)==8?14:9];",
    "0x1F",
    "#include <stdio.h>",
    "\"hello\"",
    "'A'",
    "foo + bar",
    "/* still open",
    NULL,
};

int main(int argc, char **argv) {
    init_errors_core();
    init_token_kinds();

    if (argc > 1) {
        for (int i = 1; i < argc; i++) {
            print_result(argv[i], false);
            printf("---\n");
        }
        return 0;
    }

    for (int i = 0; default_samples[i]; i++) {
        print_result(default_samples[i], false);
        printf("---\n");
    }
    return 0;
}
