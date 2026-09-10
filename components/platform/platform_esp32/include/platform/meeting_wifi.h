#pragma once
#include <stdbool.h>
#include <stddef.h>
#include "services/meeting_status.h"

// Owns Wi-Fi and its provisioning portal; credentials use a dedicated NVS namespace.
bool meeting_wifi_init(void);
bool meeting_wifi_poll(void); // True when a successful setup returns to standby.
void meeting_wifi_request_setup(void);
bool meeting_wifi_connect(const char *ssid, const char *password, bool use_saved);
bool meeting_wifi_connected(void);
bool meeting_wifi_saved(void);
// Connect first, persist only after success. Never starts the AP portal.
bool meeting_wifi_provision(const char *ssid, const char *password);
// Official provisioning owns the radio between begin/end. Our confirmed NVS
// record stays authoritative until the encrypted finish endpoint commits it.
bool meeting_wifi_external_begin(void);
bool meeting_wifi_external_commit(void);
bool meeting_wifi_external_end(bool committed);
// Counters only: never log client addresses, hostnames, or submitted passwords.
typedef struct {
    bool dns_advertised, dns_ready;
    unsigned clients, dns_queries, redirects, page_views;
} meeting_wifi_diagnostics_t;
meeting_wifi_diagnostics_t meeting_wifi_diagnostics(void);
void meeting_wifi_probe(void); // USB-only diagnostic against the device itself.
void meeting_wifi_snapshot(meeting_status_t *view);
