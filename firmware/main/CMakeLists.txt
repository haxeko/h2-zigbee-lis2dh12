idf_component_register(
    SRCS "main.c" "lis2dh12.c" "activity.c" "battery.c" "zb_badge.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_driver_i2c esp_driver_gpio esp_adc esp_timer esp_pm nvs_flash esp_hw_support
)
