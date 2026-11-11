# Activity badge firmware — ESP32-H2 + LIS2DH12, Zigbee sleepy end device.
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# Only build the components "main" actually depends on (faster build, smaller image).
set(COMPONENTS main)

project(activity_badge)
