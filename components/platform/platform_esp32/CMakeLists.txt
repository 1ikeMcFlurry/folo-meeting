# components/platform_esp32/CMakeLists.txt
set(meeting_sources "")
set(meeting_requires "")
set(meeting_assets "")
if(CONFIG_FOLO_MEETING_TRIAL)
    list(APPEND meeting_sources "src/meeting_trial.c" "src/meeting_wifi.c"
         "src/meeting_result_json.c" "src/meeting_result_check.c")
    list(APPEND meeting_requires esp_wifi esp_netif esp_event esp_websocket_client esp_http_server json)
    list(APPEND meeting_assets "assets/meeting_wifi.html")
endif()
if(CONFIG_FOLO_MEETING_COMPANION)
    list(APPEND meeting_sources "src/meeting_cloud.c" "src/meeting_ble.c" "src/meeting_companion.c" "src/meeting_provision.c")
    list(APPEND meeting_requires esp_http_client lwip network_provisioning)
endif()
idf_component_register(
    SRCS "src/disp_st7789.c" "src/lvgl_port_setup.c" "src/screen_capture_lvgl.c"
         "src/btn_iot_button.c"
         "src/audio_es8311.c" "src/platform_sys.c" "src/ble_config.c"
         "src/ble_scan.c" "src/kv_nvs.c" "src/jpeg_store.c" "src/jpeg_view.c"
         "src/png_decode.c" "src/battery_cw2017.c"
         "src/identity_nvs.c" "src/provision_console.c" "src/token_mac_esp.c"
         "src/audio_store.c"
         "src/avatar_store.c"
         ${meeting_sources}
    INCLUDE_DIRS "include"
    EMBED_TXTFILES ${meeting_assets}
    REQUIRES ports services driver esp_lcd
    PRIV_REQUIRES esp_lvgl_port button esp_codec_dev esp_hw_support bt nvs_flash
                  esp_driver_i2c esp_driver_i2s esp_partition esp_new_jpeg esp_adc
                  mbedtls esp_driver_usb_serial_jtag esp_rom spiffs esp_http_server esp_http_client lwip ${meeting_requires})
