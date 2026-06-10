#ifndef SHIVYC_REGEX_HELPERS_H
#define SHIVYC_REGEX_HELPERS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>

typedef struct {
    char **data;
    size_t size;
    size_t capacity;
} StrList;

void StrList_init(StrList *list);
void StrList_push(StrList *list, const char *item);
size_t StrList_len(const StrList *list);
const char *StrList_get(const StrList *list, size_t index);

bool float_const_fullmatch(const char *token_str);
bool int_const_fullmatch(const char *token_str);
bool identifier_fullmatch(const char *token_str);
StrList *str_splitlines(const char *text);

#endif
