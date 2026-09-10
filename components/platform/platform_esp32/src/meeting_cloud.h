#pragma once
#include "cJSON.h"
#include <stdbool.h>
#include <stddef.h>

// Returned JSON belongs to caller. Requests never log credentials or URLs.
cJSON *meeting_settings_load(void);
bool meeting_settings_save(cJSON *value);
bool meeting_clock_sync(void);
cJSON *meeting_tingwu(const char *method, const char *path, const char *query, cJSON *body);
cJSON *meeting_download_result(const char *url, size_t limit);
cJSON *meeting_feishu(const char *method, const char *path, cJSON *body, const char *token);
char *meeting_feishu_token(void);
const char *meeting_json_string(const cJSON *obj, const char *key);
void meeting_json_wipe(cJSON *obj);
unsigned meeting_cloud_error(void);
