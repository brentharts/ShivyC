#ifndef SHIVYCX_RUNTIME_H
#define SHIVYCX_RUNTIME_H

#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

/* Bump-pointer arena for compiler-lifetime allocations. */
typedef struct Arena {
    char *base;
    char *ptr;
    char *end;
} Arena;

static inline void arena_init(Arena *arena, size_t capacity) {
    arena->base = (char *)malloc(capacity ? capacity : (size_t)1 << 20);
    arena->ptr = arena->base;
    arena->end = arena->base + (capacity ? capacity : (size_t)1 << 20);
}

static inline void *arena_alloc(Arena *arena, size_t size, size_t align) {
    size_t pad = (align - (size_t)(arena->ptr - arena->base)) & (align - 1);
    char *next = arena->ptr + pad;
    char *after = next + size;
    if (after > arena->end) {
        abort();
    }
    arena->ptr = after;
    return next;
}

static inline void arena_free(Arena *arena) {
    free(arena->base);
    arena->base = NULL;
    arena->ptr = NULL;
    arena->end = NULL;
}

static inline char *arena_strdup(Arena *arena, const char *s) {
    size_t n = strlen(s) + 1;
    char *copy = (char *)arena_alloc(arena, n, 1);
    memcpy(copy, s, n);
    return copy;
}

typedef struct {
    int *data;
    size_t size;
    size_t capacity;
} IntList;

static inline void IntList_init(IntList *list) {
    list->data = NULL;
    list->size = 0;
    list->capacity = 0;
}

static inline void IntList_push(IntList *list, int item) {
    if (list->size + 1 > list->capacity) {
        size_t cap = list->capacity ? list->capacity * 2 : 8;
        list->data = (int *)realloc(list->data, cap * sizeof(int));
        list->capacity = cap;
    }
    list->data[list->size++] = item;
}

static inline size_t IntList_len(const IntList *list) {
    return list->size;
}

static inline int IntList_get(const IntList *list, size_t index) {
    return list->data[index];
}

static inline void IntList_clear(IntList *list) {
    list->size = 0;
}

/* Generic growable pointer array. */
#define DEFINE_LIST(T, Name) \
    typedef struct { \
        T **data; \
        size_t size; \
        size_t capacity; \
    } Name; \
    static inline void Name##_init(Name *list) { \
        list->data = NULL; \
        list->size = 0; \
        list->capacity = 0; \
    } \
    static inline void Name##_reserve(Name *list, size_t cap) { \
        if (cap <= list->capacity) return; \
        size_t new_cap = list->capacity ? list->capacity * 2 : 8; \
        while (new_cap < cap) new_cap *= 2; \
        list->data = (T **)realloc(list->data, new_cap * sizeof(T *)); \
        list->capacity = new_cap; \
    } \
    static inline void Name##_push(Name *list, T *item) { \
        Name##_reserve(list, list->size + 1); \
        list->data[list->size++] = item; \
    } \
    static inline T *Name##_get(const Name *list, size_t index) { \
        return list->data[index]; \
    } \
    static inline void Name##_set(Name *list, size_t index, T *item) { \
        list->data[index] = item; \
    } \
    static inline void Name##_extend(Name *list, const Name *other) { \
        Name##_reserve(list, list->size + other->size); \
        for (size_t i = 0; i < other->size; i++) { \
            list->data[list->size++] = other->data[i]; \
        } \
    } \
    static inline void Name##_clear(Name *list) { \
        list->size = 0; \
    } \
    static inline void Name##_free(Name *list) { \
        free(list->data); \
        Name##_init(list); \
    } \
    static inline size_t Name##_len(const Name *list) { \
        return list->size; \
    } \
    static inline T *Name##_pop_back(Name *list) { \
        if (list->size == 0) return NULL; \
        return list->data[--list->size]; \
    } \
    static inline T *Name##_last(const Name *list) { \
        return list->size ? list->data[list->size - 1] : NULL; \
    } \
    static inline void Name##_remove_at(Name *list, size_t index) { \
        if (index >= list->size) return; \
        for (size_t j = index + 1; j < list->size; j++) { \
            list->data[j - 1] = list->data[j]; \
        } \
        list->size--; \
    }

#include <ctype.h>

static inline bool str_contains_char(const char *set, char ch) {
    for (const char *p = set; *p; p++) {
        if (*p == ch) return true;
    }
    return false;
}

static inline bool c_str_isdigit(const char *s) {
    return s && s[0] && s[1] == '\0' && isdigit((unsigned char)s[0]);
}

static inline bool c_str_startswith(const char *s, const char *prefix) {
    return strncmp(s, prefix, strlen(prefix)) == 0;
}

static inline int str_to_int_base(const char *s, int base) {
    return (int)strtol(s, NULL, base);
}

static inline char *str_concat(const char *a, const char *b) {
    size_t la = strlen(a);
    size_t lb = strlen(b);
    char *out = (char *)malloc(la + lb + 1);
    if (!out) return NULL;
    memcpy(out, a, la);
    memcpy(out + la, b, lb + 1);
    return out;
}

static inline char *str_append_char(const char *a, char c) {
    char buf[2] = {c, '\0'};
    return str_concat(a, buf);
}

static inline const char *char_to_str(char c) {
    char *s = (char *)malloc(2);
    if (!s) return "";
    s[0] = c;
    s[1] = '\0';
    return s;
}
static inline char c_tolower_char(char c) {
    return (char)tolower((unsigned char)c);
}

static inline char *str_slice(const char *s, size_t start, size_t end) {
    size_t len = end > start ? end - start : 0;
    char *out = (char *)malloc(len + 1);
    if (!out) return NULL;
    if (len) memcpy(out, s + start, len);
    out[len] = '\0';
    return out;
}

typedef struct {
    const char *data;
    size_t len;
} StringView;

static inline StringView string_view(const char *data, size_t len) {
    StringView sv = {data, len};
    return sv;
}

#endif /* SHIVYCX_RUNTIME_H */
