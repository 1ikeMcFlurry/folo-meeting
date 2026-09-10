#pragma once
#include <stddef.h>

typedef struct {
    unsigned passed, total;
    size_t input_bytes, projected_bytes;
} meeting_result_checks_t;

// Synthetic tests only, run explicitly while idle, using an existing buffer.
meeting_result_checks_t meeting_result_check(char *buffer, size_t capacity);
