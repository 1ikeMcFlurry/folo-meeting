#pragma once
#include <stdint.h>
#include <stdbool.h>

// Display data only; no audio buffers, credentials, or provider dependencies.
typedef enum {
    MEETING_BOOTING, MEETING_IDLE, MEETING_WIFI, MEETING_CLOUD,
    MEETING_RECORDING, MEETING_FINISHING, MEETING_ENDED, MEETING_ERROR,
    MEETING_SUMMARY_READY, MEETING_SUMMARY_FAILED,
    MEETING_WIFI_SETUP, MEETING_WIFI_VERIFY, MEETING_WIFI_SETUP_ERROR, MEETING_WIFI_SETUP_DONE
} meeting_state_t;

typedef struct {
    meeting_state_t state;
    uint32_t elapsed_ms;
    uint8_t level; // Measured microphone peak, 0..100; zero when not recording.
    bool wifi_connected;
    bool companion, ble_enabled, ble_connected, document_ready, ble_provision;
    uint32_t pairing_code;
    char setup_ssid[24];
    char wifi_message[80];
} meeting_status_t;
