#ifndef SHIVYC_ERRORS_CORE_H
#define SHIVYC_ERRORS_CORE_H

#include <stdbool.h>
#include <stddef.h>
#include "shivycx_runtime.h"

typedef struct Position Position;
struct Position {
    const char *file;
    int line;
    int col;
    const char *full_line;
};

typedef struct Range Range;
struct Range {
    Position *start;
    Position *end;
};

typedef struct Tagged Tagged;
struct Tagged {
    const char *c;
    Position *p;
    Range *r;
};

typedef struct CompilerError CompilerError;
struct CompilerError {
    const char *descrip;
    Range *range;
    bool warning;
};

typedef struct {
    CompilerError **data;
    size_t size;
    size_t capacity;
} CompilerErrorList;

void CompilerErrorList_init(CompilerErrorList *list);
void CompilerErrorList_push(CompilerErrorList *list, CompilerError *item);
size_t CompilerErrorList_len(const CompilerErrorList *list);
CompilerError *CompilerErrorList_get(const CompilerErrorList *list, size_t index);
void CompilerErrorList_clear(CompilerErrorList *list);

typedef struct ErrorCollector ErrorCollector;
struct ErrorCollector {
    CompilerErrorList *issues;
};

extern ErrorCollector *error_collector;
extern CompilerError *shivycx_pending_error;

void init_errors_core(void);
void clear_pending_error(void);
void set_pending_compiler_error(const char *descrip, Range *range);
CompilerError *take_pending_error(void);

Position *Position_new(const char *file, int line, int col, const char *full_line);
Range *Range_new(Position *start, Position *end);
Tagged *Tagged_new(const char *c, Position *p);
ErrorCollector *ErrorCollector_new(void);
void ErrorCollector_add(ErrorCollector *self, CompilerError *issue);
bool ErrorCollector_ok(ErrorCollector *self);
void ErrorCollector_clear(ErrorCollector *self);
size_t ErrorCollector_issue_count(const ErrorCollector *self);
CompilerError *ErrorCollector_issue_at(const ErrorCollector *self, size_t index);
CompilerError *CompilerError_new(const char *descrip, Range *range);
Position *position_add_col(Position *pos, int delta);

#endif
