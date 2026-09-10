# host_test/CMakeLists.txt —— 业务层 PC 单测工程(linux target)
# 仅挂载纯 C 组件 ports + services(通过 components/ 下的符号链接),
# 不引入 platform_esp32/presentation/app(它们依赖 IDF/LVGL,无法在 linux 编译)。
cmake_minimum_required(VERSION 3.16)

# 只构建 main 及其依赖闭包(services→ports、unity 及核心组件)。
set(COMPONENTS main)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# 本机只装了 libbsd 运行库、缺 libbsd-dev 头文件;IDF 的 linux 端口(freertos 等)
# 需要 <bsd/string.h> / <bsd/sys/cdefs.h>。这里指向本地解包的头文件与 .so 符号链接,
# 避免依赖系统级安装(BSD_INC / BSD_LIB 由命令行 -D 传入)。
if(DEFINED BSD_INC)
    idf_build_set_property(COMPILE_OPTIONS "-I${BSD_INC}" APPEND)
endif()
if(DEFINED BSD_LIB)
    idf_build_set_property(LINK_OPTIONS "-L${BSD_LIB}" APPEND)
    idf_build_set_property(LINK_OPTIONS "-lbsd" APPEND)
endif()

project(host_test)
