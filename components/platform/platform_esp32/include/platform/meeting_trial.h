#pragma once
#include "hal/hal_audio.h"
#include "services/meeting_status.h"

// Provider session bound, not a claim of validated recording endurance.
#define FOLO_MEETING_MAX_SECONDS 86400

meeting_status_t platform_meeting_trial_status(void);
// Nonblocking; safe from the hardware button callback.
void platform_meeting_trial_stop(void);
void platform_meeting_trial_button(void);
void platform_meeting_trial_setup(void);
// Optional idle-only display capture, with audio stopped.
void platform_meeting_trial_capture_hook(void (*capture)(void));

// Dedicated experimental application; never returns, never persists audio.
void platform_meeting_trial_run(hal_audio_t *audio);
