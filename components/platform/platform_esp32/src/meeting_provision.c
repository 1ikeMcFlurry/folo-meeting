#include "meeting_provision.h"
#include "meeting_ble.h"
#include "platform/meeting_wifi.h"
#include "network_provisioning/manager.h"
#include "network_provisioning/scheme_ble.h"
#include "protocomm_ble.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "mbedtls/platform_util.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { OFF, PENDING, SELECT_NETWORK, CONNECTING, CONNECTED, FAILED, SAVED };
static atomic_int state, exit_mode, failure;
static int64_t start_at, expires_at, stop_at;
static bool initialized, transport_registered;
static char pop[33], service_name[24];
static uint8_t address[6];
static const char service_uuid[] = "f0101000-464f-4c4f-4d45-455400000001";
static const uint8_t uuid_le[16] = {1,0,0,0,0x54,0x45,0x45,0x4d,0x4f,0x4c,0x4f,0x46,0,0x10,0x10,0xf0};

bool meeting_provision_busy(void) { return atomic_load(&state) != OFF; }
void meeting_provision_cancel(void) {
    // Once the encrypted commit succeeds, cancellation cannot roll it back.
    int expected = 0;
    atomic_compare_exchange_strong(&exit_mode, &expected, 2);
}

bool meeting_provision_request(cJSON *out) {
    if (meeting_provision_busy()) return false;
    uint8_t bytes[16]; esp_fill_random(bytes, sizeof bytes);
    for (unsigned i=0; i<sizeof bytes; ++i) snprintf(pop+i*2, 3, "%02x", bytes[i]);
    mbedtls_platform_zeroize(bytes, sizeof bytes);
    esp_fill_random(address, sizeof address); address[5] |= 0xc0;
    snprintf(service_name, sizeof service_name, "Folo-WiFi-%02X%02X%02X", address[2],address[1],address[0]);
    char formatted[18];
    snprintf(formatted, sizeof formatted, "%02X:%02X:%02X:%02X:%02X:%02X",
             address[5],address[4],address[3],address[2],address[1],address[0]);
    cJSON_AddStringToObject(out,"name",service_name);
    cJSON_AddStringToObject(out,"address",formatted);
    cJSON_AddStringToObject(out,"uuid",service_uuid);
    cJSON_AddStringToObject(out,"pop",pop);
    cJSON_AddNumberToObject(out,"security",1);
    atomic_store(&exit_mode,0); atomic_store(&failure,0);
    stop_at=0; start_at=esp_timer_get_time()+2500000;
    expires_at=start_at+300000000;
    atomic_store(&state,PENDING);
    return true;
}

static void provision_event(void *arg, network_prov_cb_event_t event, void *data) {
    (void)arg;
    if (atomic_load(&exit_mode)) return;
    switch(event) {
    case NETWORK_PROV_WIFI_CRED_RECV: atomic_store(&state,CONNECTING); break;
    case NETWORK_PROV_WIFI_CRED_SUCCESS: atomic_store(&state,CONNECTED); break;
    case NETWORK_PROV_WIFI_CRED_FAIL:
        atomic_store(&failure,data ? *(network_prov_wifi_sta_fail_reason_t *)data : -1);
        atomic_store(&state,FAILED); break;
    default: break;
    }
}

static void transport_event(void *arg, esp_event_base_t base, int32_t event, void *data) {
    (void)arg; (void)base; (void)data;
    if (event==PROTOCOMM_TRANSPORT_BLE_DISCONNECTED) meeting_provision_cancel();
}

// This endpoint runs inside the authenticated Security 1 session, after the
// phone has observed the official Wi-Fi status (including an assigned IP).
static esp_err_t finish_request(uint32_t session, const uint8_t *input, ssize_t length,
                               uint8_t **output, ssize_t *output_length, void *priv) {
    (void)session; (void)priv;
    bool commit=length==6 && !memcmp(input,"commit",6);
    bool cancel=length==6 && !memcmp(input,"cancel",6);
    bool ok=false;
    if (commit && atomic_load(&exit_mode)==1) ok=true; // Idempotent confirmation.
    else if (commit && atomic_load(&state)==CONNECTED && !atomic_load(&exit_mode)) {
        ok=meeting_wifi_external_commit();
        if (ok) { atomic_store(&exit_mode,1); atomic_store(&state,SAVED); }
    } else if (cancel) { meeting_provision_cancel(); ok=true; }
    const char *reply=ok ? "{\"ok\":true}" : "{\"ok\":false}";
    *output=(uint8_t *)strdup(reply);
    if (!*output) return ESP_ERR_NO_MEM;
    *output_length=strlen(reply);
    return ESP_OK;
}

static bool start(void) {
    if (!meeting_ble_stop() || !meeting_wifi_external_begin()) return false;
    // No FREE_BTDM: recording and standby must restart BLE in the same boot.
    network_prov_mgr_config_t config = {
        .scheme=network_prov_scheme_ble,
        .scheme_event_handler=NETWORK_PROV_EVENT_HANDLER_NONE,
        .app_event_handler={.event_cb=provision_event},
        .network_prov_wifi_conn_cfg={.wifi_conn_attempts=3}
    };
    esp_log_level_set("network_prov_mgr",ESP_LOG_WARN);
    esp_err_t error=network_prov_mgr_init(config);
    if (error!=ESP_OK) return false;
    initialized=true;
    if (esp_event_handler_register(PROTOCOMM_TRANSPORT_BLE_EVENT,PROTOCOMM_TRANSPORT_BLE_DISCONNECTED,
                                   transport_event,NULL)!=ESP_OK) return false;
    transport_registered=true;
    // A separate temporary BLE address avoids Android's bonded GATT cache
    // confusing the control service with the official provisioning service.
    if (network_prov_scheme_ble_set_service_uuid((uint8_t *)uuid_le)!=ESP_OK ||
        network_prov_scheme_ble_set_random_addr(address)!=ESP_OK ||
        network_prov_mgr_disable_auto_stop(1000)!=ESP_OK ||
        network_prov_mgr_endpoint_create("folo-finish")!=ESP_OK) return false;
    if (network_prov_mgr_start_provisioning(NETWORK_PROV_SECURITY_1,pop,service_name,NULL)!=ESP_OK) return false;
    if (network_prov_mgr_endpoint_register("folo-finish",finish_request,NULL)!=ESP_OK) return false;
    atomic_store(&state,SELECT_NETWORK);
    printf("{\"event\":\"ble_provision_started\",\"security\":1}\n");
    return true;
}

bool meeting_provision_poll(void) {
    if (!meeting_provision_busy()) return false;
    int64_t now=esp_timer_get_time();
    if (now>=expires_at) meeting_provision_cancel();
    if (atomic_load(&state)==PENDING && now>=start_at && !atomic_load(&exit_mode)) {
        if (!start()) { printf("{\"event\":\"ble_provision_start_failed\"}\n"); meeting_provision_cancel(); }
    }
    if (atomic_load(&exit_mode) && !stop_at) stop_at=now+1800000;
    if (stop_at && now>=stop_at) {
        bool committed=atomic_load(&exit_mode)==1;
        if (initialized) { network_prov_mgr_deinit(); initialized=false; }
        if (transport_registered) {
            esp_event_handler_unregister(PROTOCOMM_TRANSPORT_BLE_EVENT,PROTOCOMM_TRANSPORT_BLE_DISCONNECTED,transport_event);
            transport_registered=false;
        }
        mbedtls_platform_zeroize(pop,sizeof pop);
        bool ble=meeting_ble_start(false);
        bool restored=meeting_wifi_external_end(committed);
        printf("{\"event\":\"ble_provision_finished\",\"committed\":%s,\"wifi_restored\":%s,\"ble\":%s}\n",
               committed?"true":"false",restored?"true":"false",ble?"true":"false");
        atomic_store(&state,OFF);
    }
    return true;
}

void meeting_provision_snapshot(meeting_status_t *view) {
    int value=atomic_load(&state);
    if (value==OFF) return;
    view->ble_provision=true; view->pairing_code=UINT32_MAX;
    view->state=value==FAILED ? MEETING_WIFI_SETUP_ERROR : value==SAVED ? MEETING_WIFI_SETUP_DONE :
        value==CONNECTING || value==CONNECTED ? MEETING_WIFI_VERIFY : MEETING_WIFI_SETUP;
    snprintf(view->setup_ssid,sizeof view->setup_ssid,"%s",service_name);
    const char *message=value==CONNECTED ? "网络已连接, 等待手机确认" : value==CONNECTING ? "正在连接网络, 请稍候" :
        value==FAILED ? (atomic_load(&failure)==NETWORK_PROV_WIFI_STA_AUTH_ERROR ? "密码错误, 请在手机重试" : "找不到网络, 请在手机重试") :
        value==SAVED ? "连接成功, Wi-Fi 已保存" : "请在手机选择 Wi-Fi";
    snprintf(view->wifi_message,sizeof view->wifi_message,"%s",message);
}
