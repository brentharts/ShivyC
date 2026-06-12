#ifndef SHIVYC_TOKEN_KINDS_H
#define SHIVYC_TOKEN_KINDS_H

#include "tokens.h"
#include "shivycx_runtime.h"

DEFINE_LIST(TokenKind, TokenKindList)

extern TokenKindList *symbol_kinds;
extern TokenKindList *keyword_kinds;

extern TokenKind *bool_kw;
extern TokenKind *char_kw;
extern TokenKind *short_kw;
extern TokenKind *int_kw;
extern TokenKind *long_kw;
extern TokenKind *float_kw;
extern TokenKind *double_kw;
extern TokenKind *signed_kw;
extern TokenKind *unsigned_kw;
extern TokenKind *void_kw;
extern TokenKind *return_kw;
extern TokenKind *if_kw;
extern TokenKind *else_kw;
extern TokenKind *while_kw;
extern TokenKind *do_kw;
extern TokenKind *switch_kw;
extern TokenKind *case_kw;
extern TokenKind *default_kw;
extern TokenKind *goto_kw;
extern TokenKind *for_kw;
extern TokenKind *break_kw;
extern TokenKind *continue_kw;
extern TokenKind *auto_kw;
extern TokenKind *register_kw;
extern TokenKind *static_kw;
extern TokenKind *extern_kw;
extern TokenKind *struct_kw;
extern TokenKind *union_kw;
extern TokenKind *enum_kw;
extern TokenKind *const_kw;
extern TokenKind *volatile_kw;
extern TokenKind *restrict_kw;
extern TokenKind *atomic_kw;
extern TokenKind *typedef_kw;
extern TokenKind *sizeof_kw;
extern TokenKind *alignof_kw;
extern TokenKind *asm_kw;
extern TokenKind *incr;
extern TokenKind *decr;
extern TokenKind *plusequals;
extern TokenKind *minusequals;
extern TokenKind *starequals;
extern TokenKind *divequals;
extern TokenKind *modequals;
extern TokenKind *orequals;
extern TokenKind *andequals;
extern TokenKind *xorequals;
extern TokenKind *lshiftequals;
extern TokenKind *rshiftequals;
extern TokenKind *twoequals;
extern TokenKind *notequal;
extern TokenKind *bool_and;
extern TokenKind *bool_or;
extern TokenKind *lbitshift;
extern TokenKind *rbitshift;
extern TokenKind *ltoe;
extern TokenKind *gtoe;
extern TokenKind *lt;
extern TokenKind *gt;
extern TokenKind *plus;
extern TokenKind *minus;
extern TokenKind *slash;
extern TokenKind *mod;
extern TokenKind *bool_not;
extern TokenKind *compl;
extern TokenKind *bitor;
extern TokenKind *bitxor;
extern TokenKind *amp;
extern TokenKind *question;
extern TokenKind *colon;
extern TokenKind *dot;
extern TokenKind *arrow;
extern TokenKind *star;
extern TokenKind *open_paren;
extern TokenKind *close_paren;
extern TokenKind *open_brack;
extern TokenKind *close_brack;
extern TokenKind *open_sq_brack;
extern TokenKind *close_sq_brack;
extern TokenKind *comma;
extern TokenKind *equals;
extern TokenKind *dots;
extern TokenKind *dquote;
extern TokenKind *squote;
extern TokenKind *pound;
extern TokenKind *identifier;
extern TokenKind *string;
extern TokenKind *char_string;
extern TokenKind *include_file;
extern TokenKind *number;
extern TokenKind *unrecognized;
extern TokenKind *semicolon;

void init_token_kinds(void);

#endif /* SHIVYC_TOKEN_KINDS_H */
