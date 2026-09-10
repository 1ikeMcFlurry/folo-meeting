#pragma once

#include <stdbool.h>

// Validate a bounded, NUL-terminated provider message and keep only the fields
// used by the trial. Compacts in place without allocating a JSON object tree.
// On failure the buffer may be modified and must not be used.
bool meeting_result_project(char *json);
