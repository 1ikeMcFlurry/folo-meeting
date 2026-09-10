#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"
bool meeting_ble_start(bool pairing);
bool meeting_ble_stop(void);
bool meeting_ble_active(void);
bool meeting_ble_connected(void);
uint32_t meeting_ble_passkey(void); // UINT32_MAX means not showing a code.
void meeting_ble_allow_pairing(void);
cJSON *meeting_ble_take(void);
void meeting_ble_reply(cJSON *reply);
