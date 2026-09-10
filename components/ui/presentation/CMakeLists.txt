# components/presentation/CMakeLists.txt
set(meeting_ui_sources "")
if(CONFIG_FOLO_MEETING_TRIAL)
    list(APPEND meeting_ui_sources "src/ui_meeting_trial.c")
endif()
idf_component_register(
    SRCS "src/ui_profile.c" "src/ui_game.c" "src/ui_mario.c" "src/ui_reward.c"
         "src/ui_factory.c" "src/ui_selftest.c" "src/ui_pet.c" "src/ui_button_guide.c"
         "src/lv_font_cn_16.c" "src/lv_font_cn_24.c" ${meeting_ui_sources}
    INCLUDE_DIRS "include"
    REQUIRES services
    PRIV_REQUIRES lvgl)
