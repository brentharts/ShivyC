#ifndef SHIVYC_TOKEN_KINDS_H
#define SHIVYC_TOKEN_KINDS_H

#include "tokens.h"
#include "shivycx_runtime.h"

DEFINE_LIST(TokenKind, TokenKindList)

extern TokenKindList *symbol_kinds;
extern TokenKindList *keyword_kinds;

extern TokenKind *dquote;
extern TokenKind *squote;
extern TokenKind *pound;
extern TokenKind *identifier;
extern TokenKind *string;
extern TokenKind *char_string;
extern TokenKind *include_file;
extern TokenKind *number;
extern TokenKind *unrecognized;

void init_token_kinds(void);

#endif /* SHIVYC_TOKEN_KINDS_H */
