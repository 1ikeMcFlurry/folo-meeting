#pragma once
#include "cJSON.h"
#include "services/meeting_status.h"
#include <stdbool.h>

bool meeting_provision_request(cJSON *response);
bool meeting_provision_busy(void);
bool meeting_provision_poll(void);
void meeting_provision_cancel(void);
void meeting_provision_snapshot(meeting_status_t *view);
