#include "meeting_result_json.h"
#include <stddef.h>
#include <string.h>

typedef struct { const char *read; char *write; } cursor_t;

static void whitespace(cursor_t *c) {
    while (*c->read == ' ' || *c->read == '\r' || *c->read == '\n' || *c->read == '\t') ++c->read;
}

static bool digit(char ch) { return ch >= '0' && ch <= '9'; }

static bool string(cursor_t *c) {
    if (*c->read++ != '"') return false;
    while (*c->read && *c->read != '"') {
        unsigned char ch = *c->read++;
        if (ch < 0x20) return false;
        if (ch == '\\') {
            ch = *c->read;
            if (!ch) return false;
            ++c->read;
            if (ch == 'u') {
                for (unsigned i = 0; i < 4; ++i) {
                    ch = *c->read;
                    if (!digit(ch) && !(ch >= 'a' && ch <= 'f') && !(ch >= 'A' && ch <= 'F')) return false;
                    ++c->read;
                }
            } else if (!strchr("\"\\/bfnrt", ch)) return false;
        }
    }
    if (*c->read != '"') return false;
    ++c->read;
    return true;
}

static bool value(cursor_t *c, unsigned depth) {
    if (depth > 16) return false;
    whitespace(c);
    char ch = *c->read;
    if (ch == '"') return string(c);
    if (ch == '{' || ch == '[') {
        bool object = ch == '{';
        char close = object ? '}' : ']';
        ++c->read;
        whitespace(c);
        if (*c->read == close) { ++c->read; return true; }
        while (true) {
            if (object) {
                if (*c->read != '"' || !string(c)) return false;
                whitespace(c);
                if (*c->read++ != ':') return false;
            }
            if (!value(c, depth + 1)) return false;
            whitespace(c);
            if (*c->read == close) { ++c->read; return true; }
            if (*c->read++ != ',') return false;
            whitespace(c);
        }
    }
    const char *literal = ch == 't' ? "true" : ch == 'f' ? "false" : ch == 'n' ? "null" : NULL;
    if (literal) {
        size_t n = strlen(literal);
        if (strncmp(c->read, literal, n)) return false;
        c->read += n;
        return true;
    }
    if (ch == '-') ++c->read;
    if (*c->read == '0') ++c->read;
    else {
        if (!digit(*c->read)) return false;
        while (digit(*c->read)) ++c->read;
    }
    if (*c->read == '.') {
        ++c->read;
        if (!digit(*c->read)) return false;
        while (digit(*c->read)) ++c->read;
    }
    if (*c->read == 'e' || *c->read == 'E') {
        ++c->read;
        if (*c->read == '+' || *c->read == '-') ++c->read;
        if (!digit(*c->read)) return false;
        while (digit(*c->read)) ++c->read;
    }
    return true;
}

static void copy(cursor_t *c, const char *start, size_t length) {
    memmove(c->write, start, length);
    c->write += length;
}

// Keys emitted by Tingwu are ASCII. Escaped/unknown names are skipped, never
// confused with text containing the same words or with nested metadata keys.
static int field(const char *key, size_t length, unsigned section) {
    static const char *const names[][4] = {
        {"header", "payload", NULL, NULL},
        {"name", "status", NULL, NULL},
        {"index", "result", "speaker_id", "time"},
    };
    for (unsigned i = 0; i < 4 && names[section][i]; ++i) {
        if (strlen(names[section][i]) == length && !memcmp(key, names[section][i], length)) return (int)i;
    }
    return -1;
}

static bool object(cursor_t *c, unsigned section) {
    whitespace(c);
    if (*c->read != '{') return false;
    ++c->read;
    *c->write++ = '{';
    unsigned seen = 0;
    whitespace(c);
    if (*c->read == '}') { ++c->read; *c->write++ = '}'; return true; }
    while (true) {
        const char *key = c->read;
        if (*c->read != '"' || !string(c)) return false;
        size_t key_length = c->read - key;
        int id = field(key + 1, key_length - 2, section);
        whitespace(c);
        if (*c->read != ':') return false;
        ++c->read;
        whitespace(c);
        if (id >= 0) {
            if (seen & (1U << id)) return false;
            if (seen) *c->write++ = ',';
            seen |= 1U << id;
            copy(c, key, key_length);
            *c->write++ = ':';
            if (section == 0) {
                if (!object(c, (unsigned)id + 1)) return false;
            } else {
                // Only scalar output is needed; never allocate nested provider
                // word arrays or other metadata on the device's small heap.
                const char *start = c->read;
                if (*start == '{' || *start == '[' || !value(c, 0)) return false;
                copy(c, start, (size_t)(c->read - start));
            }
        } else if (!value(c, 0)) return false;
        whitespace(c);
        if (*c->read == '}') { ++c->read; *c->write++ = '}'; return true; }
        if (*c->read != ',') return false;
        ++c->read;
        whitespace(c);
    }
}

bool meeting_result_project(char *json) {
    if (!json) return false;
    cursor_t c = {.read = json, .write = json};
    if (!object(&c, 0)) return false;
    whitespace(&c);
    if (*c.read) return false;
    *c.write = 0;
    return true;
}
