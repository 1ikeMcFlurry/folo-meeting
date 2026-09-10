#include "presentation/ui_meeting_trial.h"
#include "lvgl.h"
#include <string.h>

LV_FONT_DECLARE(lv_font_cn_16);
LV_FONT_DECLARE(lv_font_cn_24);

static lv_obj_t *screen, *state_label, *clock_label, *detail, *hint, *dot, *meter;
static lv_obj_t *setup_name, *setup_hint, *setup_url;
static int previous_state = -1;
static uint32_t previous_second = UINT32_MAX;
static bool previous_wifi;
static uint32_t previous_pin=UINT32_MAX;
static bool previous_ble, previous_doc;
static char previous_message[80];

static lv_obj_t *label(const lv_font_t *font, uint32_t color, int y) {
    lv_obj_t *obj = lv_label_create(screen);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_text_align(obj, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(obj, 208);
    lv_obj_set_pos(obj, 16, y);
    return obj;
}

void ui_meeting_trial_open(void) {
    screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x20141C), 0);
    lv_obj_set_style_pad_all(screen, 0, 0);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title = label(&lv_font_cn_16, 0xF472B6, 22);
    lv_label_set_text(title, "FoloToy 会议录音");

    dot = lv_obj_create(screen);
    lv_obj_remove_style_all(dot);
    lv_obj_set_size(dot, 12, 12);
    lv_obj_set_pos(dot, 114, 60);
    lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
    state_label = label(&lv_font_cn_24, 0xFFF1F6, 84);
    clock_label = label(&lv_font_montserrat_34, 0xFFF1F6, 128);
    lv_label_set_text(clock_label, "00:00");

    meter = lv_bar_create(screen);
    lv_obj_set_size(meter, 176, 8);
    lv_obj_set_pos(meter, 32, 187);
    lv_bar_set_range(meter, 0, 100);
    lv_obj_set_style_bg_color(meter, lv_color_hex(0x49313F), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(meter, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_bg_color(meter, lv_color_hex(0x54D9B4), LV_PART_INDICATOR);
    detail = label(&lv_font_cn_16, 0xC5AFBD, 210);
    hint = label(&lv_font_cn_16, 0xEBD8E4, 267);
    setup_name = label(&lv_font_cn_16, 0xFFF1F6, 126);
    setup_hint = label(&lv_font_cn_16, 0xFFF1F6, 156);
    lv_label_set_text(setup_hint, "无需密码, 直接连接");
    setup_url = label(&lv_font_montserrat_14, 0x54D9B4, 188);
    lv_label_set_text(setup_url, "http://192.168.4.1");
    lv_screen_load(screen);
    previous_state = -1;
    previous_second = UINT32_MAX;
    const meeting_status_t initial = {.state = MEETING_BOOTING};
    ui_meeting_trial_update(&initial);
}

void ui_meeting_trial_update(const meeting_status_t *status) {
    if (!screen || !status) return;
    bool setup = status->state >= MEETING_WIFI_SETUP;
    if ((int)status->state != previous_state || status->wifi_connected != previous_wifi ||
        status->ble_enabled != previous_ble || status->pairing_code != previous_pin || status->document_ready != previous_doc ||
        strcmp(previous_message,status->wifi_message)) {
        lv_obj_t *setup_objects[] = {setup_name, setup_hint, setup_url};
        for (unsigned i = 0; i < 3; ++i) {
            if (setup) lv_obj_remove_flag(setup_objects[i], LV_OBJ_FLAG_HIDDEN);
            else lv_obj_add_flag(setup_objects[i], LV_OBJ_FLAG_HIDDEN);
        }
        if (setup) {
            lv_obj_add_flag(clock_label, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(meter, LV_OBJ_FLAG_HIDDEN);
            lv_label_set_text(setup_name, status->setup_ssid);
            lv_label_set_text(setup_hint,"无需密码, 直接连接");
            if(status->ble_provision) lv_obj_add_flag(setup_hint, LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_y(setup_name,status->ble_provision ? 132 : 126);
            lv_obj_set_y(setup_url,status->ble_provision ? 172 : 188);
            lv_obj_set_style_text_font(setup_url,status->ble_provision ? &lv_font_cn_16 : &lv_font_montserrat_14,0);
            lv_label_set_text(setup_url,status->ble_provision ? "仅支持 2.4 GHz Wi-Fi" : "http://192.168.4.1");
        } else {
            lv_obj_remove_flag(clock_label, LV_OBJ_FLAG_HIDDEN);
            lv_obj_remove_flag(meter, LV_OBJ_FLAG_HIDDEN);
        }
        const char *title = "启动中", *description = "尚未录音", *action = "请稍候";
        uint32_t color = 0xF472B6;
        switch (status->state) {
        case MEETING_IDLE:
            title = "准备录音"; description = status->wifi_connected ? "Wi-Fi 已连接\n麦克风未开启" : "麦克风未开启";
            action = "电脑发起录音\n长按上键配网"; break;
        case MEETING_WIFI:
            title = "连接网络"; description = "尚未录音"; color = 0xF2BE68; break;
        case MEETING_CLOUD:
            title = "连接听悟"; description = "网络已连接\n尚未录音"; color = 0xF2BE68; break;
        case MEETING_RECORDING:
            title = "录音中"; description = "麦克风音量\n正在实时上传";
            action = "按确定键结束录音"; color = 0xFF646F; break;
        case MEETING_FINISHING:
            title = "正在上传"; description = "录音已停止\n正在上传剩余音频"; color = 0xF2BE68; break;
        case MEETING_ENDED:
            title = "正在生成纪要"; description = "录音已停止\n音频上传完成";
            action = "请保持电脑程序运行"; color = 0x54D9B4; break;
        case MEETING_ERROR:
            title = "录音失败"; description = "麦克风已停止\n录音可能不完整";
            action = "请在电脑端重试"; color = 0xFF646F; break;
        case MEETING_SUMMARY_READY:
            title = "纪要已生成"; description = "麦克风已停止\n录音上传已完成";
            action = "请在电脑端查看纪要"; color = 0x54D9B4; break;
        case MEETING_SUMMARY_FAILED:
            title = "纪要待查询"; description = "麦克风已停止\n云端结果暂不可用";
            action = "请在电脑端查询结果"; color = 0xF2BE68; break;
        case MEETING_WIFI_SETUP:
            title = "连接设备热点"; description = status->wifi_message;
            action = "连上热点后自动打开\n未弹出可访问上方地址"; color = 0x54D9B4; break;
        case MEETING_WIFI_VERIFY:
            title = "验证网络"; description = status->wifi_message;
            action = "请保持连接设备热点"; color = 0xF2BE68; break;
        case MEETING_WIFI_SETUP_ERROR:
            title = "配网未完成"; description = status->wifi_message;
            action = "请在配网页修改后重试"; color = 0xFF646F; break;
        case MEETING_WIFI_SETUP_DONE:
            title = "Wi-Fi 已连接"; description = "网络设置已保存\n下次开机自动连接";
            action = "正在返回待机页面"; color = 0x54D9B4; break;
        default: break;
        }
        if (status->ble_provision) {
            if(status->state==MEETING_WIFI_SETUP) { title="连接 Wi-Fi"; color=0xF472B6; }
            description=status->wifi_message;
            action=status->state==MEETING_WIFI_SETUP_DONE ? "即将返回录音页面" : "请保持手机蓝牙开启\n长按上键退出配网";
        }
        if (status->companion && !setup) {
            switch(status->state) {
            case MEETING_IDLE:
                description=status->wifi_connected ? "Wi-Fi 已连接\n麦克风未开启" : "Wi-Fi 未连接\n请在手机 App 中配网";
                action="按确定键开始录音\n长按上键连接手机"; break;
            case MEETING_ENDED: action="请保持网络连接\n稍后在 App 同步纪要"; break;
            case MEETING_SUMMARY_READY:
                description=status->document_ready ? "飞书文档已保存\n打开 App 查看纪要" : "打开 App 同步纪要\n查看和编辑会议内容";
                action="按确定键开始新录音\n长按上键连接手机"; break;
            case MEETING_ERROR: action="请用手机检查配置\n长按上键连接手机"; break;
            case MEETING_SUMMARY_FAILED: action="请在 App 中\n重新获取纪要"; break;
            default: break;
            }
            if (status->pairing_code != UINT32_MAX) {
                title="蓝牙配对"; description="在手机输入配对码"; action="配对后即可配置设备";
                lv_obj_remove_flag(clock_label,LV_OBJ_FLAG_HIDDEN);
                lv_label_set_text_fmt(clock_label,"%06u",(unsigned)status->pairing_code);
            }
        }
        lv_label_set_text(state_label, title);
        lv_obj_set_style_text_color(state_label, lv_color_hex(color), 0);
        lv_obj_set_style_bg_color(dot, lv_color_hex(color), 0);
        lv_label_set_text(detail, description);
        lv_label_set_text(hint, action);
        previous_state = status->state;
        previous_wifi = status->wifi_connected;
        previous_ble = status->ble_enabled;
        previous_doc = status->document_ready;
        memcpy(previous_message,status->wifi_message,sizeof previous_message);
        if(previous_pin != status->pairing_code) previous_second=UINT32_MAX;
        previous_pin=status->pairing_code;
    }
    uint32_t seconds = status->elapsed_ms / 1000;
    if ((!status->companion || status->pairing_code==UINT32_MAX) && seconds != previous_second) {
        if (seconds >= 3600) {
            lv_label_set_text_fmt(clock_label, "%02u:%02u:%02u", (unsigned)(seconds / 3600),
                                 (unsigned)(seconds / 60 % 60), (unsigned)(seconds % 60));
        } else {
            lv_label_set_text_fmt(clock_label, "%02u:%02u", (unsigned)(seconds / 60), (unsigned)(seconds % 60));
        }
        previous_second = seconds;
    }
    lv_bar_set_value(meter, status->state == MEETING_RECORDING ? status->level : 0, LV_ANIM_OFF);
}
