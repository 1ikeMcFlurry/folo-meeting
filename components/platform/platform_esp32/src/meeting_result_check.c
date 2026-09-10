#include "meeting_result_check.h"
#include "meeting_result_json.h"
#include <stdio.h>
#include <string.h>

meeting_result_checks_t meeting_result_check(char *buffer, size_t capacity) {
    meeting_result_checks_t out = {0};
    static const struct { const char *input, *expected; } cases[] = {
        {"{}", "{}"},
        {" { \"extra\": [1, true, null, {\"result\":\"ignore\"}], \"header\":{\"name\":\"SentenceEnd\",\"status\":20000000}, \"payload\":{\"words\":[{\"text\":\"skip\"}],\"result\":\"测试\\n\\\"quote\\\"\\u4e2d\",\"index\":2} } ",
         "{\"header\":{\"name\":\"SentenceEnd\",\"status\":20000000},\"payload\":{\"result\":\"测试\\n\\\"quote\\\"\\u4e2d\",\"index\":2}}"},
        {"{\"payload\":{\"time\":1.2e+3,\"speaker_id\":\"1\",\"index\":0,\"result\":\"words ] } header\"},\"header\":{\"name\":\"SentenceEnd\"}}",
         "{\"payload\":{\"time\":1.2e+3,\"speaker_id\":\"1\",\"index\":0,\"result\":\"words ] } header\"},\"header\":{\"name\":\"SentenceEnd\"}}"},
        {"{\"payload\":{\"result\":\"a\",\"result\":\"b\"}}", NULL},
        {"{\"payload\":{\"result\":[]}}", NULL},
        {"{\"header\":null}", NULL},
        {"{\"extra\":01}", NULL},
        {"{\"extra\":-}", NULL},
        {"{\"extra\":1.}", NULL},
        {"{\"extra\":1e}", NULL},
        {"{\"extra\":tru}", NULL},
        {"{\"extra\":trueX}", NULL},
        {"{\"extra\":[1,]}", NULL},
        {"{\"extra\":{\"a\":1,}}", NULL},
        {"{\"extra\":\"\\x\"}", NULL},
        {"{\"extra\":\"\\u12\"}", NULL},
        {"{\"extra\":\"line\nfeed\"}", NULL},
        {"{\"extra\":\"unfinished\\", NULL},
        {"{}{}", NULL},
        {"[]", NULL},
        {"{\"extra\":[[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]}", NULL},
    };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
        ++out.total;
        if (strlen(cases[i].input) >= capacity) continue;
        strcpy(buffer, cases[i].input);
        bool valid = meeting_result_project(buffer);
        if (cases[i].expected ? valid && !strcmp(buffer, cases[i].expected) : !valid) ++out.passed;
    }
    const char *prefix = "{\"header\":{\"name\":\"SentenceEnd\",\"status\":20000000},\"payload\":{\"words\":[";
    const char *word = "{\"text\":\"sample\",\"start\":1234,\"end\":1356,\"confidence\":0.99}";
    const char *suffix = "],\"index\":9,\"result\":\"Synthetic meeting sample\"}}";
    const char *expected = "{\"header\":{\"name\":\"SentenceEnd\",\"status\":20000000},\"payload\":{\"index\":9,\"result\":\"Synthetic meeting sample\"}}";
    size_t length = strlen(prefix);
    ++out.total;
    if (length + strlen(suffix) + 1 < capacity) {
        strcpy(buffer, prefix);
        for (unsigned i = 0; i < 220 && length + strlen(word) + strlen(suffix) + 2 < capacity; ++i) {
            if (i) buffer[length++] = ',';
            memcpy(buffer + length, word, strlen(word));
            length += strlen(word);
        }
        strcpy(buffer + length, suffix);
        out.input_bytes = strlen(buffer);
        if (meeting_result_project(buffer) && !strcmp(buffer, expected)) ++out.passed;
        out.projected_bytes = strlen(buffer);
    }
    // Every strict prefix of a valid message must be rejected, including cuts
    // inside UTF-8/escape sequences, numbers, arrays, and object delimiters.
    const char *sample = cases[1].expected;
    for (size_t i = 0; i < strlen(sample) && i + 1 < capacity; ++i) {
        ++out.total;
        memcpy(buffer, sample, i);
        buffer[i] = 0;
        if (!meeting_result_project(buffer)) ++out.passed;
    }
    memset(buffer, 0, capacity);
    return out;
}
