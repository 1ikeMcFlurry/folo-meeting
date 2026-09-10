// Composition root for the opt-in, RAM-only meeting experiment.
#include "app/app.h"
#include "platform/platform_factory.h"
#include "platform/board_config.h"
#include "platform/meeting_trial.h"
#include "platform/platform_screen_capture.h"
#include "presentation/ui_meeting_trial.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>

static void on_button(int index, hal_btn_event_t event, void *user) {
    (void)user;
    if (index == 2 && event == HAL_BTN_PRESS) platform_meeting_trial_button();
    if (index == 0 && event == HAL_BTN_LONG) platform_meeting_trial_setup();
}

static void refresh_screen(void *arg) {
    (void)arg;
    for (;;) {
        meeting_status_t status = platform_meeting_trial_status();
        if (platform_lvgl_lock(100)) {
            ui_meeting_trial_update(&status);
            platform_lvgl_unlock();
        }
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

// Reuse the real LCD flush hook. RLE rows need no extra framebuffer; diagnostics
// are accepted only while idle and contain screen pixels, never microphone data.
static bool screen_row(int x, int y, int w, int h, const uint8_t *rgb, void *user) {
    (void)user;
    for (int row = 0; row < h; ++row) {
        printf("{\"event\":\"screen_row\",\"x\":%d,\"y\":%d,\"w\":%d,\"runs\":[", x, y + row, w);
        for (int col = 0; col < w;) {
            int offset = ((row * w) + col) * 2;
            unsigned color = rgb[offset] | ((unsigned)rgb[offset + 1] << 8);
            int count = 1;
            while (col + count < w) {
                int next = offset + count * 2;
                if (color != (rgb[next] | ((unsigned)rgb[next + 1] << 8))) break;
                ++count;
            }
            printf("%s[%d,%u]", col ? "," : "", count, color);
            col += count;
        }
        puts("]}");
    }
    fflush(stdout);
    return true;
}

static void capture_screen(void) {
    bool ok = platform_screen_capture_start();
    printf("{\"event\":\"screen_done\",\"ok\":%s}\n", ok ? "true" : "false");
    fflush(stdout);
}

void app_run(void) {
    board_config_t board = BOARD_CONFIG_DEFAULT();
    hal_display_t *display = platform_create_display(&board);
    struct _lv_display_t *lv_display = display ? platform_lvgl_init(display, &board) : NULL;
    if (lv_display && platform_lvgl_lock(0)) {
        ui_meeting_trial_open();
        platform_lvgl_unlock();
        hal_display_set_backlight(display, 75);
        platform_screen_capture_init(lv_display, screen_row, NULL, NULL);
        platform_meeting_trial_capture_hook(capture_screen);
        xTaskCreate(refresh_screen, "meeting_ui", 3072, NULL, 2, NULL);
    }
    hal_button_t *button = platform_create_button(&board);
    if (button) hal_button_on_event(button, on_button, NULL);
    hal_audio_t *audio = platform_create_audio(&board);
    platform_meeting_trial_run(audio);
}
