#include "meeting_ble.h"
#include "host/ble_hs.h"
#include "host/ble_sm.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "store/config/ble_store_config.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "mbedtls/platform_util.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
void ble_store_config_init(void);

// Dedicated protocol; does not load badge scanning/game/image services.
static const ble_uuid128_t service_id=BLE_UUID128_INIT(0x01,0x00,0x00,0x00,0x54,0x45,0x45,0x4d,0x4f,0x4c,0x4f,0x46,0x00,0x00,0x10,0xf0);
static const ble_uuid128_t rx_id=BLE_UUID128_INIT(0x02,0x00,0x00,0x00,0x54,0x45,0x45,0x4d,0x4f,0x4c,0x4f,0x46,0x00,0x00,0x10,0xf0);
static const ble_uuid128_t tx_id=BLE_UUID128_INIT(0x03,0x00,0x00,0x00,0x54,0x45,0x45,0x4d,0x4f,0x4c,0x4f,0x46,0x00,0x00,0x10,0xf0);
static SemaphoreHandle_t lock, exited;
static atomic_bool active, connected, synced;
static atomic_uint pin=UINT32_MAX;
static int64_t pair_until;
static uint8_t address_type;
static char name[24];
static char *input;
static size_t have, expected;
static int64_t fragment_at;
static cJSON *pending;
static char response[512]="{\"ready\":true,\"protocol\":1}";
static int gap(struct ble_gap_event *event, void *arg);
static void advertise(void) {
    if (!atomic_load(&active)) return;
    struct ble_hs_adv_fields fields={0};
    fields.flags=BLE_HS_ADV_F_DISC_GEN|BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128=(ble_uuid128_t *)&service_id; fields.num_uuids128=1; fields.uuids128_is_complete=1;
    if (ble_gap_adv_set_fields(&fields)) return;
    struct ble_hs_adv_fields scan={0}; scan.name=(uint8_t *)name; scan.name_len=strlen(name); scan.name_is_complete=1;
    ble_gap_adv_rsp_set_fields(&scan);
    struct ble_gap_adv_params params={.conn_mode=BLE_GAP_CONN_MODE_UND,.disc_mode=BLE_GAP_DISC_MODE_GEN,
        .itvl_min=320,.itvl_max=480};
    ble_gap_adv_start(address_type,NULL,BLE_HS_FOREVER,&params,gap,NULL);
}
static int access_gatt(uint16_t conn, uint16_t attr, struct ble_gatt_access_ctxt *ctx, void *arg) {
    (void)attr; (void)arg;
    struct ble_gap_conn_desc desc;
    if (ble_gap_conn_find(conn,&desc) || !desc.sec_state.encrypted || !desc.sec_state.authenticated)
        return BLE_ATT_ERR_INSUFFICIENT_AUTHEN;
    if (ctx->op==BLE_GATT_ACCESS_OP_READ_CHR) {
        xSemaphoreTake(lock,portMAX_DELAY);
        int rc=os_mbuf_append(ctx->om,response,strlen(response));
        xSemaphoreGive(lock);
        return rc ? BLE_ATT_ERR_INSUFFICIENT_RES : 0;
    }
    uint8_t frame[185]; uint16_t len=0;
    if (ble_hs_mbuf_to_flat(ctx->om,frame,sizeof frame,&len) || len<5) return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    size_t total=frame[0]|(frame[1]<<8), offset=frame[2]|(frame[3]<<8), count=len-4;
    int result=0;
    xSemaphoreTake(lock,portMAX_DELAY);
    int64_t now=esp_timer_get_time();
    if (now-fragment_at>10000000) { have=expected=0; if(input) mbedtls_platform_zeroize(input,4096); }
    fragment_at=now;
    if (!input || pending || total==0 || total>=4096 || offset+count>total) result=BLE_ATT_ERR_INSUFFICIENT_RES;
    else {
        if (!offset) { have=0; expected=total; memset(input,0,4096); strcpy(response,"{\"busy\":true}"); }
        if (offset!=have || total!=expected) result=BLE_ATT_ERR_INVALID_OFFSET;
        else {
            memcpy(input+have,frame+4,count); have+=count;
            if (have==expected) {
                input[have]=0; pending=cJSON_Parse(input);
                if (!cJSON_IsObject(pending)) { cJSON_Delete(pending); pending=NULL; result=BLE_ATT_ERR_UNLIKELY; }
                mbedtls_platform_zeroize(input,4096); have=expected=0;
            }
        }
    }
    xSemaphoreGive(lock); mbedtls_platform_zeroize(frame,sizeof frame);
    return result;
}
static const struct ble_gatt_svc_def services[]={
    {.type=BLE_GATT_SVC_TYPE_PRIMARY,.uuid=&service_id.u,.characteristics=(struct ble_gatt_chr_def[]){
        {.uuid=&rx_id.u,.access_cb=access_gatt,.flags=BLE_GATT_CHR_F_WRITE|BLE_GATT_CHR_F_WRITE_ENC|BLE_GATT_CHR_F_WRITE_AUTHEN},
        {.uuid=&tx_id.u,.access_cb=access_gatt,.flags=BLE_GATT_CHR_F_READ|BLE_GATT_CHR_F_READ_ENC|BLE_GATT_CHR_F_READ_AUTHEN}, {0}}}, {0}};
static int gap(struct ble_gap_event *event, void *arg) {
    (void)arg;
    switch(event->type) {
    case BLE_GAP_EVENT_CONNECT:
        atomic_store(&connected,event->connect.status==0);
        if (event->connect.status) advertise();
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        atomic_store(&connected,false); atomic_store(&pin,UINT32_MAX);
        xSemaphoreTake(lock,portMAX_DELAY);
        have=expected=0; if(input) mbedtls_platform_zeroize(input,4096);
        xSemaphoreGive(lock); advertise(); break;
    case BLE_GAP_EVENT_ADV_COMPLETE: advertise(); break;
    case BLE_GAP_EVENT_PASSKEY_ACTION: {
        if (esp_timer_get_time()>pair_until || event->passkey.params.action!=BLE_SM_IOACT_DISP) {
            ble_gap_terminate(event->passkey.conn_handle,BLE_ERR_AUTH_FAIL); break;
        }
        uint32_t value=esp_random()%1000000;
        struct ble_sm_io io={.action=BLE_SM_IOACT_DISP,.passkey=value};
        atomic_store(&pin,value);
        ble_sm_inject_io(event->passkey.conn_handle,&io); break;
    }
    case BLE_GAP_EVENT_ENC_CHANGE: atomic_store(&pin,UINT32_MAX); break;
    case BLE_GAP_EVENT_REPEAT_PAIRING:
        if (esp_timer_get_time()>pair_until) return BLE_GAP_REPEAT_PAIRING_IGNORE;
        { struct ble_gap_conn_desc desc;
          if (!ble_gap_conn_find(event->repeat_pairing.conn_handle,&desc)) ble_store_util_delete_peer(&desc.peer_id_addr); }
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    default: break;
    }
    return 0;
}
static void sync_host(void) { ble_hs_util_ensure_addr(0); ble_hs_id_infer_auto(0,&address_type); atomic_store(&synced,true); advertise(); }
static void host_task(void *arg) { (void)arg; nimble_port_run(); xSemaphoreGive(exited); vTaskDelete(NULL); }
void meeting_ble_allow_pairing(void) { pair_until=esp_timer_get_time()+300000000; }
bool meeting_ble_start(bool pairing) {
    if(pairing) meeting_ble_allow_pairing();
    if(atomic_load(&active)) return true;
    if(!lock) lock=xSemaphoreCreateMutex();
    if(!exited) exited=xSemaphoreCreateBinary();
    if(!lock || !exited) return false;
    input=calloc(1,4096); if(!input) return false;
    atomic_store(&synced,false);
    if(nimble_port_init()!=ESP_OK) { free(input); input=NULL; return false; }
    uint8_t mac[6]; esp_read_mac(mac,ESP_MAC_BT);
    snprintf(name,sizeof name,"Folo-Meeting-%02X%02X",mac[4],mac[5]);
    ble_hs_cfg.sync_cb=sync_host;
    ble_hs_cfg.sm_io_cap=BLE_HS_IO_DISPLAY_ONLY;
    ble_hs_cfg.sm_bonding=1; ble_hs_cfg.sm_mitm=1; ble_hs_cfg.sm_sc=1;
    ble_hs_cfg.sm_our_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_store_config_init(); ble_svc_gap_init(); ble_svc_gatt_init();
    if(ble_gatts_count_cfg(services) || ble_gatts_add_svcs(services) || ble_svc_gap_device_name_set(name)) {
        nimble_port_deinit(); free(input); input=NULL; return false;
    }
    atomic_store(&active,true);
    if(xTaskCreate(host_task,"meeting_ble",4096,NULL,5,NULL)!=pdPASS) {
        atomic_store(&active,false); nimble_port_deinit(); free(input); input=NULL; return false;
    }
    return true;
}
bool meeting_ble_stop(void) {
    if(!atomic_load(&active)) return true;
    // Host startup is asynchronous. Stopping while the controller is still
    // synchronizing can race its HCI event processing during deinitialization.
    for(int i=0;i<200 && !atomic_load(&synced);i++) vTaskDelay(pdMS_TO_TICKS(10));
    if(!atomic_load(&synced)) return false;
    atomic_store(&active,false); ble_gap_adv_stop();
    if(nimble_port_stop()!=0) { atomic_store(&active,true); return false; }
    if(xSemaphoreTake(exited,pdMS_TO_TICKS(3000))!=pdTRUE) return false;
    if(nimble_port_deinit()!=ESP_OK) return false;
    // No esp_bt_mem_release: this same boot must restart BLE after recording.
    atomic_store(&connected,false); atomic_store(&synced,false); atomic_store(&pin,UINT32_MAX);
    xSemaphoreTake(lock,portMAX_DELAY);
    if(input) { mbedtls_platform_zeroize(input,4096); free(input); input=NULL; }
    have=expected=0;
    if(pending) { cJSON_Delete(pending); pending=NULL; }
    xSemaphoreGive(lock);
    vTaskDelay(pdMS_TO_TICKS(30));
    return true;
}
bool meeting_ble_active(void) { return atomic_load(&active); }
bool meeting_ble_connected(void) { return atomic_load(&connected); }
uint32_t meeting_ble_passkey(void) { return atomic_load(&pin); }
cJSON *meeting_ble_take(void) {
    if(!lock) return NULL;
    xSemaphoreTake(lock,portMAX_DELAY); cJSON *out=pending; pending=NULL; xSemaphoreGive(lock); return out;
}
void meeting_ble_reply(cJSON *reply) {
    xSemaphoreTake(lock,portMAX_DELAY);
    if(!cJSON_PrintPreallocated(reply,response,sizeof response,false)) strcpy(response,"{\"ok\":false,\"error\":\"response_too_large\"}");
    xSemaphoreGive(lock);
}
