#ifndef SHIVYC_ERRORS_CORE_H
#define SHIVYC_ERRORS_CORE_H

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

typedef struct ErrorCollector ErrorCollector;
struct ErrorCollector {
    int issue_count;
};

typedef struct CompilerError CompilerError;
struct CompilerError {
    const char *descrip;
    Range *range;
    bool warning;
};

extern ErrorCollector *error_collector;

void init_errors_core(void);

Position *Position_new(const char *file, int line, int col, const char *full_line);
Range *Range_new(Position *start, Position *end);
Tagged *Tagged_new(const char *c, Position *p);
ErrorCollector *ErrorCollector_new(void);
void ErrorCollector_add(ErrorCollector *self, CompilerError *issue);
bool ErrorCollector_ok(ErrorCollector *self);
void ErrorCollector_clear(ErrorCollector *self);
CompilerError *CompilerError_new(const char *descrip, Range *range);

#endif
