#pragma once
#include "services/meeting_status.h"

// Caller holds the LVGL lock. This screen never stores or displays audio/text.
void ui_meeting_trial_open(void);
void ui_meeting_trial_update(const meeting_status_t *status);
