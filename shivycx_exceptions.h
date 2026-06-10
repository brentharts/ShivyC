#ifndef SHIVYCX_EXCEPTIONS_H
#define SHIVYCX_EXCEPTIONS_H

#include "errors_core.h"

extern CompilerError *shivycx_pending_error;

static inline void shivycx_clear_pending_error(void) {
    shivycx_pending_error = NULL;
}

#define SHIVYCX_RAISE(err) do { shivycx_pending_error = (err); return; } while(0)

#endif /* SHIVYCX_EXCEPTIONS_H */
