# host_test/main/CMakeLists.txt
# 直接编译 services/test 下的用例源(不把 test 目录当独立组件注册)。
set(TEST_DIR "${CMAKE_CURRENT_LIST_DIR}/../../components/core/services/test")

idf_component_register(
    SRCS "test_runner.c"
         "${TEST_DIR}/test_profile_ctl.c"
         "${TEST_DIR}/test_ble_match.c"
         "${TEST_DIR}/test_screen_capture.c"
    INCLUDE_DIRS "${TEST_DIR}"
    REQUIRES services unity)

# TEST_CASE 通过 __attribute__((constructor)) 注册;这些对象文件无被引用符号,
# 普通静态库链接会被丢弃 → 0 个测试。强制 whole-archive 保留它们。
idf_component_set_property(main WHOLE_ARCHIVE TRUE)
