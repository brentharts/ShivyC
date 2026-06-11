#ifndef SHIVYC_TOKENS_H
#define SHIVYC_TOKENS_H

#include "errors_core.h"
#include "shivycx_runtime.h"

typedef struct TokenKind TokenKind;
struct TokenKind {
    const char *text_repr;
};

typedef struct Token Token;
struct Token {
    TokenKind *kind;
    const char *content;
    const char *rep;
    Range *r;
    bool wide;
    IntList *int_content;
    bool use_int_content;
    int logical_line;
};

TokenKind *TokenKind_new(const char *text_repr);
Token *Token_new(TokenKind *kind, const char *content, const char *rep, Range *r);

#endif /* SHIVYC_TOKENS_H */
