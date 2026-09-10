#include "meeting_cloud.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_random.h"
#include "esp_sntp.h"
#include "nvs.h"
#include "mbedtls/md.h"
#include "mbedtls/platform_util.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>

static unsigned last_error;
unsigned meeting_cloud_error(void) { return last_error; }
const char *meeting_json_string(const cJSON *obj, const char *key) {
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsString(item) ? item->valuestring : "";
}
void meeting_json_wipe(cJSON *obj) {
    if (!obj) return;
    for (cJSON *i = obj->child; i; i = i->next) {
        if (cJSON_IsString(i) && i->valuestring) mbedtls_platform_zeroize(i->valuestring, strlen(i->valuestring));
        if (i->child) meeting_json_wipe(i);
    }
}
cJSON *meeting_settings_load(void) {
    nvs_handle_t h;
    char *raw = NULL;
    size_t n = 0;
    if (nvs_open("meeting_user", NVS_READONLY, &h) == ESP_OK) {
        if (nvs_get_str(h, "settings", NULL, &n) == ESP_OK && n <= 8192) {
            raw = calloc(1, n);
            if (raw && nvs_get_str(h, "settings", raw, &n) != ESP_OK) { free(raw); raw = NULL; }
        }
        nvs_close(h);
    }
    cJSON *obj = raw ? cJSON_Parse(raw) : NULL;
    if (raw) { mbedtls_platform_zeroize(raw, n); free(raw); }
    return obj ? obj : cJSON_CreateObject();
}
bool meeting_settings_save(cJSON *value) {
    char *raw = cJSON_PrintUnformatted(value);
    if (!raw) return false;
    nvs_handle_t h;
    bool ok = false;
    if (strlen(raw) < 8192 && nvs_open("meeting_user", NVS_READWRITE, &h) == ESP_OK) {
        ok = nvs_set_str(h, "settings", raw) == ESP_OK && nvs_commit(h) == ESP_OK;
        nvs_close(h);
    }
    mbedtls_platform_zeroize(raw, strlen(raw)); free(raw);
    return ok;
}
bool meeting_clock_sync(void) {
    static bool started;
    if (!started) {
        esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
        esp_sntp_setservername(0, "ntp.aliyun.com");
        esp_sntp_setservername(1, "pool.ntp.org");
        esp_sntp_init(); started = true;
    }
    for (int i = 0; time(NULL) < 1750000000 && i < 100; ++i) vTaskDelay(pdMS_TO_TICKS(100));
    return time(NULL) >= 1750000000;
}
static void hex(const unsigned char *bytes, size_t n, char *out) {
    const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < n; ++i) { out[i*2] = digits[bytes[i] >> 4]; out[i*2+1] = digits[bytes[i] & 15]; }
    out[n*2] = 0;
}
static void sha(const char *data, char out[65]) {
    unsigned char hash[32];
    mbedtls_md(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), (const unsigned char *)data, strlen(data), hash);
    hex(hash, 32, out);
}
static cJSON *receive(esp_http_client_handle_t client, const char *body, size_t limit) {
    last_error = 0;
    cJSON *result = NULL;
    char *raw = NULL;
    if (esp_http_client_open(client, body ? strlen(body) : 0) != ESP_OK) { last_error = 1001; goto done; }
    if (body) {
        size_t sent = 0, len = strlen(body);
        while (sent < len) {
            int n = esp_http_client_write(client, body + sent, len - sent);
            if (n <= 0) { last_error = 1002; goto done; }
            sent += n;
        }
    }
    if (esp_http_client_fetch_headers(client) < 0) { last_error = 1003; goto done; }
    int status = esp_http_client_get_status_code(client);
    if (status < 200 || status >= 300) { last_error = status; goto done; }
    raw = malloc(limit + 1);
    if (!raw) { last_error = 1004; goto done; }
    size_t have = 0;
    while (have < limit) {
        int n = esp_http_client_read(client, raw + have, limit - have);
        if (n < 0) { last_error = 1005; goto done; }
        if (!n) break;
        have += n;
    }
    if (!esp_http_client_is_complete_data_received(client)) { last_error = 1006; goto done; }
    raw[have] = 0;
    // Drop the TLS session before expanding JSON into nodes.
    esp_http_client_close(client); esp_http_client_cleanup(client); client=NULL;
    result = cJSON_Parse(raw);
    if (!result) last_error = 1007;
done:
    if (raw) { mbedtls_platform_zeroize(raw, limit + 1); free(raw); }
    if(client) { esp_http_client_close(client); esp_http_client_cleanup(client); }
    return result;
}
static esp_http_client_handle_t http(const char *url, const char *method) {
    esp_http_client_config_t config = {.url=url, .crt_bundle_attach=esp_crt_bundle_attach,
        .timeout_ms=15000, .buffer_size=1024, .buffer_size_tx=2048, .disable_auto_redirect=true};
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) { last_error = 1004; return NULL; }
    esp_http_client_set_method(client, !strcmp(method,"GET") ? HTTP_METHOD_GET :
        !strcmp(method,"PUT") ? HTTP_METHOD_PUT : !strcmp(method,"PATCH") ? HTTP_METHOD_PATCH :
        !strcmp(method,"DELETE") ? HTTP_METHOD_DELETE : HTTP_METHOD_POST);
    return client;
}
cJSON *meeting_tingwu(const char *method, const char *path, const char *query, cJSON *body) {
    cJSON *settings = meeting_settings_load(), *result = NULL;
    char *data = body ? cJSON_PrintUnformatted(body) : strdup("");
    const char *key = meeting_json_string(settings, "ak_id"), *secret = meeting_json_string(settings, "ak_secret");
    if (!data || !*key || !*secret || !meeting_clock_sync()) { last_error = 1008; goto done; }
    const char *host = "tingwu.cn-beijing.aliyuncs.com";
    const char *action = !strcmp(method,"GET") ? "GetTaskInfo" : "CreateTask";
    const char *signed_headers = "content-type;host;x-acs-action;x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version";
    char date[32], nonce[33], payload[65], digest[65], signature[65], url[256], auth[512];
    unsigned char random[16], mac[32]; esp_fill_random(random, sizeof random); hex(random,16,nonce);
    time_t now = time(NULL); struct tm utc; gmtime_r(&now, &utc); strftime(date,sizeof date,"%Y-%m-%dT%H:%M:%SZ",&utc);
    sha(data,payload);
    char canonical[1400];
    int n = snprintf(canonical,sizeof canonical,
        "%s\n%s\n%s\ncontent-type:application/json\nhost:%s\nx-acs-action:%s\nx-acs-content-sha256:%s\nx-acs-date:%s\nx-acs-signature-nonce:%s\nx-acs-version:2023-09-30\n\n%s\n%s",
        method,path,query ? query : "",host,action,payload,date,nonce,signed_headers,payload);
    if (n < 0 || n >= sizeof canonical) { last_error=1009; goto done; }
    sha(canonical,digest);
    char to_sign[100]; snprintf(to_sign,sizeof to_sign,"ACS3-HMAC-SHA256\n%s",digest);
    mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), (const unsigned char *)secret,strlen(secret),
        (const unsigned char *)to_sign,strlen(to_sign),mac); hex(mac,32,signature);
    snprintf(auth,sizeof auth,"ACS3-HMAC-SHA256 Credential=%s,SignedHeaders=%s,Signature=%s",key,signed_headers,signature);
    snprintf(url,sizeof url,"https://%s%s%s%s",host,path,query && *query ? "?" : "",query ? query : "");
    esp_http_client_handle_t client = http(url,method);
    if (!client) goto done;
    esp_http_client_set_header(client,"Content-Type","application/json");
    esp_http_client_set_header(client,"x-acs-action",action);
    esp_http_client_set_header(client,"x-acs-content-sha256",payload);
    esp_http_client_set_header(client,"x-acs-date",date);
    esp_http_client_set_header(client,"x-acs-signature-nonce",nonce);
    esp_http_client_set_header(client,"x-acs-version","2023-09-30");
    esp_http_client_set_header(client,"Authorization",auth);
    result = receive(client, *data ? data : NULL, 8192);
    if (result) {
        cJSON *code = cJSON_GetObjectItemCaseSensitive(result,"Code");
        if (!((cJSON_IsString(code) && !strcmp(code->valuestring,"0")) || (cJSON_IsNumber(code) && code->valueint==0))) {
            cJSON_Delete(result); result=NULL; last_error=1010;
        }
    }
done:
    if (data) { mbedtls_platform_zeroize(data,strlen(data)); free(data); }
    meeting_json_wipe(settings); cJSON_Delete(settings);
    return result;
}
cJSON *meeting_download_result(const char *url, size_t limit) {
    // Upgrade provider-returned HTTP capabilities before any network operation.
    if(!url || strlen(url)>4096) { last_error=1012; return NULL; }
    char *secure=NULL;
    if(!strncmp(url,"http://",7)) {
        secure=malloc(strlen(url)+2); if(!secure) { last_error=1004; return NULL; }
        strcpy(secure,"https://"); strcat(secure,url+7); url=secure;
    }
    cJSON *result=NULL;
    if(strncmp(url,"https://",8)) { last_error=1012; goto done; }
    const char *end=strchr(url+8,'/'), *host_end=end;
    if(end && end-url>12 && !memcmp(end-4,":443",4)) host_end=end-4;
    if(!end || host_end-(url+8)<=13 || memchr(url+8,'@',end-url-8) || memcmp(host_end-13,".aliyuncs.com",13) || strchr(url,'#')) {
        last_error=1012; goto done;
    }
    esp_http_client_handle_t client=http(url,"GET");
    result=client ? receive(client,NULL,limit) : NULL;
done:
    if(secure) { mbedtls_platform_zeroize(secure,strlen(secure)); free(secure); }
    return result;
}
cJSON *meeting_feishu(const char *method, const char *path, cJSON *body, const char *token) {
    char url[320]; snprintf(url,sizeof url,"https://open.feishu.cn%s",path);
    esp_http_client_handle_t client=http(url,method);
    if (!client) return NULL;
    esp_http_client_set_header(client,"Content-Type","application/json; charset=utf-8");
    if (token) {
        size_t n=strlen(token)+8; char *auth=malloc(n);
        if (!auth) { esp_http_client_cleanup(client); return NULL; }
        snprintf(auth,n,"Bearer %s",token); esp_http_client_set_header(client,"Authorization",auth);
        mbedtls_platform_zeroize(auth,n); free(auth);
    }
    char *data=body ? cJSON_PrintUnformatted(body) : NULL;
    if (body && !data) { esp_http_client_cleanup(client); return NULL; }
    cJSON *result=receive(client,data,16384);
    if (data) { mbedtls_platform_zeroize(data,strlen(data)); free(data); }
    cJSON *code=cJSON_GetObjectItemCaseSensitive(result,"code");
    if (code && (!cJSON_IsNumber(code) || code->valueint!=0)) { cJSON_Delete(result); result=NULL; last_error=1011; }
    return result;
}
char *meeting_feishu_token(void) {
    cJSON *settings=meeting_settings_load(), *body=cJSON_CreateObject();
    const char *refresh=meeting_json_string(settings,"feishu_refresh_token");
    if (!*refresh) { cJSON_Delete(settings); cJSON_Delete(body); return NULL; }
    cJSON_AddStringToObject(body,"grant_type","refresh_token");
    cJSON_AddStringToObject(body,"client_id",meeting_json_string(settings,"feishu_app_id"));
    cJSON_AddStringToObject(body,"client_secret",meeting_json_string(settings,"feishu_app_secret"));
    cJSON_AddStringToObject(body,"refresh_token",refresh);
    cJSON *result=meeting_feishu("POST","/open-apis/authen/v2/oauth/token",body,NULL);
    const char *access=meeting_json_string(result,"access_token");
    const char *next=meeting_json_string(result,"refresh_token");
    char *token=NULL;
    if (*access && *next) {
        cJSON_DeleteItemFromObjectCaseSensitive(settings,"feishu_refresh_token");
        cJSON_AddStringToObject(settings,"feishu_refresh_token",next);
        if (meeting_settings_save(settings)) token=strdup(access);
    }
    meeting_json_wipe(result); cJSON_Delete(result);
    meeting_json_wipe(body); cJSON_Delete(body);
    meeting_json_wipe(settings); cJSON_Delete(settings);
    return token;
}
