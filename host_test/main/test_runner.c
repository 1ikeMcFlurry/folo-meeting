// host_test/main/test_runner.c —— 非交互式运行全部 TEST_CASE
#include "unity.h"
#include <stdlib.h>

void app_main(void)
{
    UNITY_BEGIN();
    unity_run_all_tests();
    int failures = UNITY_END();
    exit(failures == 0 ? 0 : 1);
}
