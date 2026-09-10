#pragma once
#include "cJSON.h"
#include "services/meeting_status.h"
void meeting_companion_init(void);
void meeting_companion_poll(void);
void meeting_companion_button(void);
void meeting_companion_setup(void);
bool meeting_companion_take_start(void);
cJSON *meeting_companion_prepare(void);
void meeting_companion_finished(bool complete, unsigned elapsed_ms);
void meeting_companion_snapshot(meeting_status_t *view);
cJSON *meeting_companion_command(cJSON *request);
