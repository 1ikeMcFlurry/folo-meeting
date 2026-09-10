# components/app/CMakeLists.txt
if(CONFIG_FOLO_MEETING_TRIAL)
    set(app_source "src/app_meeting_trial.c")
else()
    set(app_source "src/app.c")
endif()
idf_component_register(
    SRCS "${app_source}"
    INCLUDE_DIRS "include"
    REQUIRES ports services presentation platform_esp32 esp_timer esp_app_format esp_hw_support)
