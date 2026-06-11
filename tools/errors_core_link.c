/* CompilerErrorList helpers declared in errors_core.h. */

#include "errors_core.h"
#include <stdlib.h>

void CompilerErrorList_init(CompilerErrorList *list) {
    list->data = NULL;
    list->size = 0;
    list->capacity = 0;
}

void CompilerErrorList_push(CompilerErrorList *list, CompilerError *item) {
    if (list->size + 1 > list->capacity) {
        size_t cap = list->capacity ? list->capacity * 2 : 8;
        list->data = (CompilerError **)realloc(list->data, cap * sizeof(CompilerError *));
        list->capacity = cap;
    }
    list->data[list->size++] = item;
}

size_t CompilerErrorList_len(const CompilerErrorList *list) {
    return list->size;
}

CompilerError *CompilerErrorList_get(const CompilerErrorList *list, size_t index) {
    return list->data[index];
}

void CompilerErrorList_clear(CompilerErrorList *list) {
    list->size = 0;
}

size_t ErrorCollector_issue_count(const ErrorCollector *self) {
    if (!self || !self->issues) {
        return 0;
    }
    return CompilerErrorList_len(self->issues);
}

CompilerError *ErrorCollector_issue_at(const ErrorCollector *self, size_t index) {
    return CompilerErrorList_get(self->issues, index);
}
