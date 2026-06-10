#ifndef SHIVYC_LEXER_CORE_H
#define SHIVYC_LEXER_CORE_H

#include "errors_core.h"
#include "tokens.h"
#include "token_kinds.h"
#include "shivycx_runtime.h"

DEFINE_LIST(Tagged, TaggedList)
DEFINE_LIST(TaggedList, TaggedListList)
DEFINE_LIST(Token, TokenList)

typedef struct {
    TokenList *tokens;
    bool in_comment;
} TokenizeLineResult;

TaggedListList *split_to_tagged_lines(const char *text, const char *filename);
void join_extended_lines(TaggedListList *lines);
TokenizeLineResult tokenize_line(TaggedList *line, bool in_comment);
TokenList *tokenize(const char *code, const char *filename);
TokenizeLineResult tokenize_text_line(const char *text, const char *filename, bool in_comment);

#endif /* SHIVYC_LEXER_CORE_H */
