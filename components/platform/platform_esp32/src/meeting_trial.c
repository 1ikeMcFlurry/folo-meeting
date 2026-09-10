// Opt-in trial only: mic -> bounded RAM queue -> verified TLS -> Tingwu.
#include "sdkconfig.h"
#include "platform/meeting_trial.h"
#include "platform/meeting_wifi.h"
#include "meeting_result_json.h"
#include "meeting_result_check.h"
#ifdef CONFIG_FOLO_MEETING_COMPANION
#include "meeting_companion.h"
#include "meeting_ble.h"
#include "meeting_cloud.h"
#endif
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "cJSON.h"
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/time.h>
#ifdef CONFIG_FOLO_MEETING_COMPANION
#include <stdarg.h>
// Diagnostics may be dropped if no host is reading. They must never block an
// independent recording or prevent the cloud task from being closed.
static int diagnostic_printf(const char *format,...) {
    char line[1024]; va_list args; va_start(args,format);
    int size=vsnprintf(line,sizeof line,format,args); va_end(args);
    if(size<=0 || size>=sizeof line) return 0;
    return usb_serial_jtag_write_bytes(line,size,0);
}
#define printf diagnostic_printf
#define puts(value) diagnostic_printf("%s\n",value)
#endif

#define WIFI_UP BIT0
#define WS_UP BIT1
#define STT_UP BIT2
#define STT_DONE BIT3
#define FAILED BIT4
#define MIC_DONE BIT5
#define FRAME_SIZE 640 // 20 ms, 16 kHz mono S16LE
#define QUEUE_FRAMES 50 // One second of jitter, never an unbounded backlog
#define SEND_FRAMES 5 // One 100 ms / 3200-byte PCM message instead of 50 small sends/s
#define TRIAL_LINE_MAX 4096
#define RESULT_MAX 16384

static EventGroupHandle_t events;
static QueueHandle_t frames;
static hal_audio_t *mic;
static atomic_bool recording;
static atomic_int screen_state = MEETING_BOOTING;
static atomic_int stop_reason; // 0=duration, 1=button, 2=serial, 3=Wi-Fi setup
static atomic_uint captured_frames;
static atomic_uint mic_level;
static atomic_int capture_error;
static atomic_uint queue_peak;
static void (*capture_hook)(void);
static unsigned capture_frames;
static char wire_task[33];
static char *result_text;
static size_t result_have;
static int64_t last_partial_emit_us;
static struct {
    bool valid, complete;
    uint32_t sent, captured;
    uint32_t max_send_ms, send_calls, queue_peak;
    int reason;
    char task_id[33];
} finished;

meeting_status_t platform_meeting_trial_status(void) {
    meeting_status_t view = {
        .state = atomic_load(&screen_state),
        .elapsed_ms = atomic_load(&captured_frames) * 20,
        .level = atomic_load(&mic_level),
    };
    if (view.state == MEETING_RECORDING && !atomic_load(&recording)) view.state = MEETING_FINISHING;
    if (view.state != MEETING_RECORDING) view.level = 0;
    meeting_wifi_snapshot(&view);
#ifdef CONFIG_FOLO_MEETING_COMPANION
    meeting_companion_snapshot(&view);
#endif
    return view;
}

static void request_stop(int reason) {
    if (!atomic_load(&recording)) return;
    int expected = 0;
    atomic_compare_exchange_strong(&stop_reason, &expected, reason);
    atomic_store(&recording, false);
}

void platform_meeting_trial_stop(void) { request_stop(1); }
void platform_meeting_trial_button(void) {
#ifdef CONFIG_FOLO_MEETING_COMPANION
    meeting_companion_button();
#else
    platform_meeting_trial_stop();
#endif
}
void platform_meeting_trial_setup(void) {
#ifdef CONFIG_FOLO_MEETING_COMPANION
    meeting_companion_setup();
#else
    request_stop(3); meeting_wifi_request_setup();
#endif
}
void platform_meeting_trial_capture_hook(void (*capture)(void)) { capture_hook = capture; }

static void status(const char *name) {
    if (strcmp(name, "init_failed") == 0 || strcmp(name, "invalid_config") == 0 ||
        strcmp(name, "mic_stalled") == 0) atomic_store(&screen_state, MEETING_ERROR);
    meeting_status_t view = platform_meeting_trial_status();
    meeting_wifi_diagnostics_t wifi = meeting_wifi_diagnostics();
    printf("{\"event\":\"%s\",\"heap\":%u,\"min_heap\":%u,\"ui_state\":%d,\"elapsed_ms\":%u,"
           "\"level\":%u,\"largest_block\":%u,\"wifi_saved\":%s,\"wifi_connected\":%s,\"wifi_setup\":%s,"
           "\"portal_dns_advertised\":%s,\"portal_dns_ready\":%s,\"portal_clients\":%u,"
           "\"portal_dns_queries\":%u,\"portal_redirects\":%u,\"portal_page_views\":%u,"
           "\"max_recording_seconds\":%u,\"companion\":%s,\"ble_enabled\":%s,\"ble_connected\":%s,\"reset_reason\":%d,\"uptime_ms\":%u}\n", name,
           (unsigned)esp_get_free_heap_size(), (unsigned)esp_get_minimum_free_heap_size(),
           view.state, (unsigned)view.elapsed_ms, (unsigned)view.level,
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),
           meeting_wifi_saved() ? "true" : "false", meeting_wifi_connected() ? "true" : "false",
           view.state >= MEETING_WIFI_SETUP ? "true" : "false",
           wifi.dns_advertised ? "true" : "false", wifi.dns_ready ? "true" : "false",
           wifi.clients, wifi.dns_queries, wifi.redirects, wifi.page_views, FOLO_MEETING_MAX_SECONDS,
           view.companion ? "true" : "false", view.ble_enabled ? "true" : "false", view.ble_connected ? "true" : "false",
           (int)esp_reset_reason(),(unsigned)(esp_timer_get_time()/1000));
    fflush(stdout);
}

static void emit_finished(void) {
    if (!finished.valid) return;
    const char *reason = !finished.complete ? "error" : finished.reason == 1 ? "button" :
                         finished.reason == 2 ? "serial" : finished.reason == 3 ? "setup" : "duration";
    printf("{\"event\":\"stream_finished\",\"task_id\":\"%s\",\"complete\":%s,\"audio_bytes\":%u,"
           "\"captured_bytes\":%u,\"end_reason\":\"%s\",\"min_heap\":%u,"
           "\"max_send_ms\":%u,\"send_calls\":%u,\"queue_peak\":%u}\n",
           finished.task_id, finished.complete ? "true" : "false", (unsigned)finished.sent,
           (unsigned)finished.captured, reason, (unsigned)esp_get_minimum_free_heap_size(),
           (unsigned)finished.max_send_ms, (unsigned)finished.send_calls, (unsigned)finished.queue_peak);
    fflush(stdout);
}

static void random_id(char out[33]) {
    uint8_t bytes[16];
    esp_fill_random(bytes, sizeof bytes);
    for (int i = 0; i < 16; ++i) snprintf(out + i * 2, 3, "%02x", bytes[i]);
}

static bool wait_for(EventBits_t wanted, unsigned milliseconds) {
    EventBits_t bits = xEventGroupWaitBits(events, wanted | FAILED, pdFALSE, pdFALSE,
                                         pdMS_TO_TICKS(milliseconds));
    return (bits & wanted) && !(bits & FAILED);
}

static void transcript(char *text) {
    // Word-level timing/confidence arrays can expand into tens of KiB of cJSON
    // nodes. Validate and discard unused fields in place before allocating.
    if (!meeting_result_project(text)) {
        printf("{\"event\":\"provider_json_error\",\"code\":1}\n");
        xEventGroupSetBits(events, FAILED);
        return;
    }
    cJSON *root = cJSON_Parse(text);
    if (!root) {
        printf("{\"event\":\"provider_json_error\",\"code\":2}\n");
        xEventGroupSetBits(events, FAILED);
        return;
    }
    const cJSON *header = cJSON_GetObjectItemCaseSensitive(root, "header");
    const cJSON *name = cJSON_GetObjectItemCaseSensitive(header, "name");
    const cJSON *code = cJSON_GetObjectItemCaseSensitive(header, "status");
    if (!cJSON_IsString(name) || (cJSON_IsNumber(code) && code->valueint != 20000000)) {
        printf("{\"event\":\"provider_error\",\"code\":%d}\n", cJSON_IsNumber(code) ? code->valueint : 0);
        fflush(stdout);
        xEventGroupSetBits(events, FAILED);
    } else if (strcmp(name->valuestring, "TranscriptionStarted") == 0) {
        xEventGroupSetBits(events, STT_UP);
    } else if (strcmp(name->valuestring, "TranscriptionCompleted") == 0) {
        xEventGroupSetBits(events, STT_DONE);
    } else if (strcmp(name->valuestring, "TaskFailed") == 0) {
        xEventGroupSetBits(events, FAILED);
    } else if (strcmp(name->valuestring, "SentenceEnd") == 0 ||
               strcmp(name->valuestring, "TranscriptionResultChanged") == 0) {
#ifdef CONFIG_FOLO_MEETING_COMPANION
        // Final text is fetched by the phone. USB output must never hold up
        // the audio path when this device operates without a computer.
        cJSON_Delete(root); return;
#endif
        bool final = strcmp(name->valuestring, "SentenceEnd") == 0;
        int64_t now = esp_timer_get_time();
        // Interim hypotheses replace each other. Bound USB rendering to 5 Hz
        // so repeated long sentences cannot stall the receive callback. Always
        // deliver final sentences, including when they follow an interim update.
        if (!final && now - last_partial_emit_us < 200000) { cJSON_Delete(root); return; }
        last_partial_emit_us = now;
        const cJSON *payload = cJSON_GetObjectItemCaseSensitive(root, "payload");
        cJSON *out = cJSON_CreateObject();
        if (out) {
            cJSON_AddStringToObject(out, "event", "transcript");
            cJSON_AddBoolToObject(out, "final", final);
            const char *keys[] = {"index", "result", "speaker_id", "time"};
            for (unsigned i = 0; i < sizeof keys / sizeof keys[0]; ++i) {
                cJSON *field = cJSON_DetachItemFromObjectCaseSensitive((cJSON *)payload, keys[i]);
                if (field) cJSON_AddItemToObject(out, keys[i], field);
            }
            // Parsing owns its scalar strings now, so reuse the receive buffer
            // for USB output rather than allocate another growing JSON buffer.
            if (cJSON_PrintPreallocated(out, result_text, RESULT_MAX, false)) {
                puts(result_text);
                fflush(stdout);
            }
            cJSON_Delete(out);
        }
    }
    cJSON_Delete(root);
}

static void websocket_event(void *arg, esp_event_base_t base, int32_t id, void *opaque) {
    (void)arg; (void)base;
    esp_websocket_event_data_t *data = opaque;
    if (id == WEBSOCKET_EVENT_CONNECTED) {
        xEventGroupSetBits(events, WS_UP);
    } else if (id == WEBSOCKET_EVENT_ERROR) {
        xEventGroupSetBits(events, FAILED);
    } else if (id == WEBSOCKET_EVENT_DISCONNECTED) {
        if (!(xEventGroupGetBits(events) & STT_DONE)) xEventGroupSetBits(events, FAILED);
    } else if (id == WEBSOCKET_EVENT_DATA && (data->op_code == 1 || data->op_code == 0)) {
        // Reassemble transport chunks, with a hard bound on provider JSON size.
        if (data->payload_offset == 0) result_have = 0;
        if (data->data_len < 0 || data->payload_len < 0 || data->payload_offset < 0 ||
            data->payload_len >= RESULT_MAX || (size_t)data->payload_offset != result_have ||
            !result_text || result_have + (size_t)data->data_len >= RESULT_MAX) {
            printf("{\"event\":\"provider_json_error\",\"code\":3}\n");
            xEventGroupSetBits(events, FAILED);
            return;
        }
        memcpy(result_text + result_have, data->data_ptr, (size_t)data->data_len);
        result_have += (size_t)data->data_len;
        if (result_have == (size_t)data->payload_len) {
            result_text[result_have] = 0;
            transcript(result_text);
        }
    }
}

static bool send_command(esp_websocket_client_handle_t ws, const char *name) {
    char message_id[33], body[384];
    random_id(message_id);
    int n = snprintf(body, sizeof body,
        "{\"header\":{\"message_id\":\"%s\",\"task_id\":\"%s\","
        "\"namespace\":\"SpeechTranscriber\",\"name\":\"%s\",\"appkey\":\"default\"},"
        "\"payload\":{}}", message_id, wire_task, name);
    return n > 0 && (size_t)n < sizeof body &&
        esp_websocket_client_send_text(ws, body, n, pdMS_TO_TICKS(3000)) == n;
}

static void capture_task(void *arg) {
    (void)arg;
    uint8_t frame[FRAME_SIZE];
    for (unsigned i = 0; i < capture_frames && atomic_load(&recording); ++i) {
        size_t got = 0;
        if (hal_audio_read(mic, frame, sizeof frame, &got) != 0 || got != sizeof frame) {
            atomic_store(&capture_error, 1);
            xEventGroupSetBits(events, FAILED);
            break;
        }
        if (xQueueSend(frames, frame, 0) != pdTRUE) {
            atomic_store(&capture_error, 2);
            xEventGroupSetBits(events, FAILED);
            break;
        }
        atomic_fetch_add(&captured_frames, 1);
        unsigned queued = uxQueueMessagesWaiting(frames);
        if (queued > atomic_load(&queue_peak)) atomic_store(&queue_peak, queued);
        unsigned peak = 0;
        for (unsigned n = 0; n < sizeof frame; n += 2) {
            int sample = (int16_t)(frame[n] | ((unsigned)frame[n + 1] << 8));
            unsigned magnitude = sample < 0 ? (unsigned)-sample : (unsigned)sample;
            if (magnitude > peak) peak = magnitude;
        }
        // Coarse logarithmic meter: useful for speech, and no floating-point DSP.
        unsigned level = 0;
        for (unsigned threshold = 64; threshold <= 16384; threshold *= 2)
            if (peak >= threshold) level += 11;
        atomic_store(&mic_level, level);
    }
    atomic_store(&recording, false);
    atomic_store(&mic_level, 0);
    xEventGroupSetBits(events, MIC_DONE);
    vTaskDelete(NULL);
}

static const char *string_field(cJSON *obj, const char *key) {
    cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsString(item) ? item->valuestring : NULL;
}

static bool allowed_url(const char *url) {
    if (!url || strncmp(url, "wss://", 6) != 0) return false;
    const char *end = strchr(url + 6, '/');
    if (!end) return false;
    const char *host_end = end;
    if (end - (url + 6) > 4 && memcmp(end - 4, ":443", 4) == 0) host_end -= 4;
    size_t host_len = (size_t)(host_end - (url + 6));
    const char suffix[] = ".aliyuncs.com";
    size_t suffix_len = sizeof suffix - 1;
    return host_len > suffix_len &&
        memcmp(host_end - suffix_len, suffix, suffix_len) == 0 &&
        memchr(url + 6, '@', host_len) == NULL && strlen(url) < 3072;
}

static void run_meeting(cJSON *config) {
    const char *ssid = string_field(config, "ssid");
    const char *password = string_field(config, "password");
    const char *url = string_field(config, "url");
    const char *task_id = string_field(config, "task_id");
    bool use_saved = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(config, "use_device_wifi"));
    cJSON *seconds = cJSON_GetObjectItemCaseSensitive(config, "seconds");
    cJSON *epoch = cJSON_GetObjectItemCaseSensitive(config, "epoch");
    if ((!use_saved && (!ssid || !*ssid || strlen(ssid) > 32 || !password || strlen(password) > 64)) ||
        !allowed_url(url) || !cJSON_IsNumber(seconds) || seconds->valueint < 1 ||
        seconds->valueint > FOLO_MEETING_MAX_SECONDS || seconds->valuedouble != seconds->valueint ||
        !cJSON_IsNumber(epoch) || epoch->valuedouble < 1700000000 ||
        !task_id || strlen(task_id) != 32 || strspn(task_id, "0123456789abcdef") != 32) {
        status("invalid_config"); return;
    }
    struct timeval now = {.tv_sec = (time_t)epoch->valuedouble};
    settimeofday(&now, NULL);
    xEventGroupClearBits(events, 0xFFFFFF);
    memset(&finished, 0, sizeof finished);
    memcpy(finished.task_id, task_id, 32);
    esp_websocket_client_handle_t ws = NULL;
    bool producer_started = false, complete = false;
    uint32_t sent = 0;
    uint32_t max_send_ms = 0, send_calls = 0;
    last_partial_emit_us = 0;
    atomic_store(&captured_frames, 0);
    atomic_store(&mic_level, 0);
    atomic_store(&stop_reason, 0);
    atomic_store(&capture_error, 0);
    atomic_store(&queue_peak, 0);
    atomic_store(&screen_state, MEETING_WIFI);
    if (!meeting_wifi_connect(ssid, password, use_saved)) goto cleanup;
    status("wifi_connected");
    atomic_store(&screen_state, MEETING_CLOUD);
    // Reserve the largest contiguous allocation before TLS fragments the heap.
    frames = xQueueCreate(QUEUE_FRAMES, FRAME_SIZE);
    if (!frames) { atomic_store(&capture_error, 3); goto cleanup; }
    result_text=calloc(1,RESULT_MAX);
    if(!result_text) { atomic_store(&capture_error, 4); goto cleanup; }
    esp_websocket_client_config_t cfg = {
        .uri = url, .crt_bundle_attach = esp_crt_bundle_attach,
        .headers = "X-NLS-Token: default\r\n",
        .disable_auto_reconnect = true,
#ifdef CONFIG_FOLO_MEETING_COMPANION
        .buffer_size = 1024, // Client fragments outgoing messages; receive is reassembled above.
#else
        .buffer_size = 4096,
#endif
        .task_stack = 6144, .network_timeout_ms = 10000,
        .ping_interval_sec = 8, .pingpong_timeout_sec = 16,
    };
    ws = esp_websocket_client_init(&cfg);
    if (!ws) goto cleanup;
    esp_websocket_register_events(ws, WEBSOCKET_EVENT_ANY, websocket_event, NULL);
    if (esp_websocket_client_start(ws) != ESP_OK || !wait_for(WS_UP, 20000)) goto cleanup;
    status("wss_connected");
    random_id(wire_task);
    if (!send_command(ws, "StartTranscription") || !wait_for(STT_UP, 15000)) goto cleanup;
    status("stt_started");
    if (hal_audio_set_sample_rate(mic, 16000, 16, 1) != 0) { atomic_store(&capture_error, 5); goto cleanup; }
    hal_audio_set_out_mute(mic, true);
    capture_frames = (unsigned)seconds->valueint * 50;
    atomic_store(&recording, true);
    if (xTaskCreate(capture_task, "meeting_mic", 4096, NULL, 6, NULL) != pdPASS) { atomic_store(&capture_error, 6); goto cleanup; }
    producer_started = true;
    atomic_store(&screen_state, MEETING_RECORDING);
    status("recording");
    int64_t deadline = esp_timer_get_time() + ((int64_t)seconds->valueint + 10) * 1000000;
    while (!(xEventGroupGetBits(events) & FAILED) && meeting_wifi_connected() && esp_timer_get_time() < deadline) {
        uint8_t batch[FRAME_SIZE * SEND_FRAMES], stop;
        if (usb_serial_jtag_read_bytes(&stop, 1, 0) == 1) {
            if (stop == '!') request_stop(2);
            else if (stop == '?') status("recording_progress");
        }
        if (xQueueReceive(frames, batch, pdMS_TO_TICKS(40)) == pdTRUE) {
            unsigned count = 1;
            // 25 ms rounds down to 20 ms at this board's tick rate and races
            // the next capture frame, producing many short sends. Two frame
            // periods give the producer scheduling room while bounding stop lag.
            while (count < SEND_FRAMES && xQueueReceive(frames, batch + count * FRAME_SIZE,
                                                       pdMS_TO_TICKS(40)) == pdTRUE) ++count;
            size_t bytes = count * FRAME_SIZE;
            int64_t send_start = esp_timer_get_time();
            int written = esp_websocket_client_send_bin(ws, (const char *)batch, bytes, pdMS_TO_TICKS(2000));
            uint32_t send_ms = (esp_timer_get_time() - send_start + 999) / 1000;
            if (send_ms > max_send_ms) max_send_ms = send_ms;
            ++send_calls;
            if (written != bytes) break;
            sent += bytes;
        } else if (xEventGroupGetBits(events) & MIC_DONE) {
            // A user stop is successful only after draining every captured frame.
            // MIC_DONE makes the final capture count stable before this comparison.
            complete = sent == atomic_load(&captured_frames) * FRAME_SIZE &&
                (atomic_load(&stop_reason) != 0 || sent == capture_frames * FRAME_SIZE);
            break;
        }
    }
    atomic_store(&recording, false);
    atomic_store(&screen_state, MEETING_FINISHING);
    if (!send_command(ws, "StopTranscription") || !wait_for(STT_DONE, 15000)) complete = false;
cleanup:
    atomic_store(&recording, false);
    atomic_store(&screen_state, complete ? MEETING_FINISHING : MEETING_ERROR);
    if (producer_started) {
        EventBits_t done = xEventGroupWaitBits(events, MIC_DONE, pdFALSE, pdTRUE, pdMS_TO_TICKS(3000));
        // Never free a queue still owned by a blocked I2S task. Reset to reclaim safely.
        if (!(done & MIC_DONE)) { status("mic_stalled"); esp_restart(); }
    }
    if (ws) { esp_websocket_client_stop(ws); esp_websocket_client_destroy(ws); }
    free(result_text); result_text=NULL;
    if (frames) { vQueueDelete(frames); frames = NULL; }
    // Keep the verified Wi-Fi association for the next meeting. The AP portal
    // and HTTP/DNS tasks are closed before capture, leaving memory for WSS.
    atomic_store(&screen_state, complete ? MEETING_ENDED : MEETING_ERROR);
    finished.complete = complete;
    finished.sent = sent;
    finished.captured = atomic_load(&captured_frames) * FRAME_SIZE;
    finished.reason = atomic_load(&stop_reason);
    finished.max_send_ms = max_send_ms;
    finished.send_calls = send_calls;
    finished.queue_peak = atomic_load(&queue_peak);
    finished.valid = true;
    if (atomic_load(&capture_error)) {
        printf("{\"event\":\"capture_error\",\"code\":%d}\n", atomic_load(&capture_error));
        fflush(stdout);
    }
    emit_finished();
}

void platform_meeting_trial_run(hal_audio_t *audio) {
    mic = audio;
    // Keep PCM upload ahead of rendering; capture (6) and Wi-Fi remain higher.
    vTaskPrioritySet(NULL, 5);
    // Library logs can contain signed URLs. Emit only our structured status.
    esp_log_level_set("*", ESP_LOG_NONE);
    usb_serial_jtag_driver_config_t usb = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    usb.rx_buffer_size = TRIAL_LINE_MAX;
    usb.tx_buffer_size = 2048;
    events = xEventGroupCreate();
    if (!mic || !events || usb_serial_jtag_driver_install(&usb) != ESP_OK ||
        nvs_flash_init() != ESP_OK || esp_netif_init() != ESP_OK ||
        esp_event_loop_create_default() != ESP_OK) {
        status("init_failed"); return;
    }
    // Route stdio through the installed USB ring buffer. The ROM polling path
    // can drop characters in bursts (completion JSON / screen rows).
    usb_serial_jtag_vfs_use_driver();
    if (!meeting_wifi_init()) {
        status("init_failed"); return;
    }
    static char line[TRIAL_LINE_MAX];
    size_t used = 0;
    bool discard = false;
    atomic_store(&screen_state, MEETING_IDLE);
#ifdef CONFIG_FOLO_MEETING_COMPANION
    meeting_companion_init();
#endif
    status("meeting_ready");
    for (;;) {
#ifdef CONFIG_FOLO_MEETING_COMPANION
        meeting_companion_poll();
        if (meeting_companion_take_start()) {
            atomic_store(&screen_state, MEETING_CLOUD);
            cJSON *start = meeting_companion_prepare();
            if (start) {
                status("cloud_task_ready");
                run_meeting(start);
                meeting_json_wipe(start); cJSON_Delete(start);
                meeting_companion_finished(finished.complete, atomic_load(&captured_frames) * 20);
                atomic_store(&screen_state, MEETING_IDLE);
            } else atomic_store(&screen_state, MEETING_ERROR);
            status("device_session_finished");
        }
#endif
        if (meeting_wifi_poll()) {
            atomic_store(&screen_state, MEETING_IDLE);
            atomic_store(&captured_frames, 0);
            status("wifi_configured");
        }
        uint8_t ch;
        if (usb_serial_jtag_read_bytes(&ch, 1, pdMS_TO_TICKS(100)) != 1) continue;
        if (ch == '\r' || ch == '\n') {
            if (used && !discard) {
                line[used] = 0;
                cJSON *config = cJSON_Parse(line);
                const char *cmd = string_field(config, "cmd");
                if (strcmp(line, "?") == 0) { status("meeting_ready"); emit_finished(); }
                else if (cmd && strcmp(cmd, "ping") == 0) status("meeting_ready");
                else if (cmd && strcmp(cmd, "start") == 0) run_meeting(config);
#ifdef CONFIG_FOLO_MEETING_COMPANION
                else if (cmd && strcmp(cmd, "companion") == 0) {
                    cJSON *request=cJSON_GetObjectItemCaseSensitive(config,"request");
                    cJSON *reply=meeting_companion_command(request);
                    if(reply) { char *raw=cJSON_PrintUnformatted(reply); if(raw) { puts(raw); fflush(stdout); free(raw); } cJSON_Delete(reply); }
                    meeting_json_wipe(config);
                }
#endif
                else if (cmd && strcmp(cmd, "wifi_setup") == 0) platform_meeting_trial_setup();
                else if (cmd && strcmp(cmd, "portal_check") == 0) meeting_wifi_probe();
                else if (cmd && strcmp(cmd, "result_check") == 0) {
                    char *scratch=malloc(RESULT_MAX);
                    meeting_result_checks_t check = scratch ? meeting_result_check(scratch, RESULT_MAX) : (meeting_result_checks_t){0};
                    free(scratch);
                    printf("{\"event\":\"result_check\",\"passed\":%u,\"total\":%u,"
                           "\"input_bytes\":%u,\"projected_bytes\":%u}\n",
                           check.passed, check.total, (unsigned)check.input_bytes, (unsigned)check.projected_bytes);
                    fflush(stdout);
                }
                else if (cmd && strcmp(cmd, "screenshot") == 0 && capture_hook) capture_hook();
                else if (cmd && strcmp(cmd, "summary") == 0) {
                    const char *result = string_field(config, "status");
                    // Only a clean finished recording can be promoted to summary success.
                    // Never erase a recording failure with a later cloud result.
                    int state = atomic_load(&screen_state);
                    if (state == MEETING_ENDED || state == MEETING_SUMMARY_FAILED) {
                        atomic_store(&screen_state, result && strcmp(result, "ready") == 0
                            ? MEETING_SUMMARY_READY : MEETING_SUMMARY_FAILED);
                    }
                    status("summary_updated");
                }
                else status("invalid_command");
                // Clear credential-containing input before returning to idle.
                if (config) {
                    const char *fields[] = {"ssid", "password", "url"};
                    for (unsigned i = 0; i < 3; ++i) {
                        cJSON *field = cJSON_GetObjectItemCaseSensitive(config, fields[i]);
                        if (cJSON_IsString(field)) memset(field->valuestring, 0, strlen(field->valuestring));
                    }
                    cJSON_Delete(config);
                }
            }
            memset(line, 0, sizeof line);
            used = 0; discard = false;
        } else if (!discard && used < sizeof line - 1) {
            line[used++] = (char)ch;
        } else {
            discard = true;
        }
    }
}
