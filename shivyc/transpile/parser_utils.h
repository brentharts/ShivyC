#ifndef SHIVYC_PARSER_UTILS_H
#define SHIVYC_PARSER_UTILS_H

#include "errors_core.h"
#include "tokens.h"
#include "token_kinds.h"
#include "shivycx_runtime.h"

typedef struct SimpleSymbolTable SimpleSymbolTable;

typedef struct ParserError ParserError;
struct ParserError {
    const char *descrip;
    Range *range;
    int amount_parsed;
    bool warning;
};

#define PARSER_ERROR_AT 1
#define PARSER_ERROR_GOT 2
#define PARSER_ERROR_AFTER 3

extern SimpleSymbolTable *symbols;
extern TokenList *tokens;
extern ParserError *best_error;
extern ParserError *shivycx_pending_parser_error;
extern const char *cur_func_name;

void init_parser_utils(void);
void reset_parse_state(void);
bool has_remaining_tokens(int index);
SimpleSymbolTable *SimpleSymbolTable_new(void);
void SimpleSymbolTable_new_scope(SimpleSymbolTable *self);
void SimpleSymbolTable_end_scope(SimpleSymbolTable *self);
void SimpleSymbolTable_add_symbol(SimpleSymbolTable *self, const char *name, bool is_typedef);
bool SimpleSymbolTable_is_typedef(SimpleSymbolTable *self, const char *name);
StrBoolMapList *SimpleSymbolTable_snapshot(SimpleSymbolTable *self);
void SimpleSymbolTable_restore(SimpleSymbolTable *self, StrBoolMapList *snap);

ParserError *ParserError_new(const char *descrip, Range *range, int amount_parsed);
void set_pending_parser_error(ParserError *err);
void clear_pending_parser_error(void);
ParserError *take_pending_parser_error(void);
ParserError *build_parser_error(const char *message, int index, int message_type);
void raise_error(const char *err, int index, int error_type);
StrBoolMapList *log_error_begin(void);
void log_error_caught(StrBoolMapList *symbols_bak, ParserError *err);
bool token_is(int index, TokenKind *kind);
bool token_in(int index, TokenKindList *kinds);
int match_token(int index, TokenKind *kind, int message_type, const char *message);
Range *token_range(int start, int end);

#endif
