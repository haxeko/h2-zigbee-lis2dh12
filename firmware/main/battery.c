#include "battery.h"

#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "board.h"

static const char *TAG = "battery";

#define ADC_SAMPLES 8

esp_err_t battery_read_mv(uint16_t *millivolts)
{
    adc_oneshot_unit_handle_t adc = NULL;
    adc_cali_handle_t cali = NULL;
    esp_err_t ret = ESP_OK;
    int sum_mv = 0;

    const adc_oneshot_unit_init_cfg_t unit_cfg = {.unit_id = ADC_UNIT_1};
    const adc_oneshot_chan_cfg_t chan_cfg = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    const adc_cali_curve_fitting_config_t cali_cfg = {
        .unit_id = ADC_UNIT_1,
        .chan = BOARD_VBAT_ADC_CHANNEL,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };

    /* Connect the divider and let the 10 nF node capacitor charge. */
    gpio_set_level(BOARD_VBAT_EN_GPIO, 1);
    vTaskDelay(pdMS_TO_TICKS(BOARD_VBAT_SETTLE_MS));

    ESP_GOTO_ON_ERROR(adc_oneshot_new_unit(&unit_cfg, &adc), out, TAG, "ADC init failed");
    ESP_GOTO_ON_ERROR(adc_oneshot_config_channel(adc, BOARD_VBAT_ADC_CHANNEL, &chan_cfg), out, TAG, "ADC channel failed");
    ESP_GOTO_ON_ERROR(adc_cali_create_scheme_curve_fitting(&cali_cfg, &cali), out, TAG, "ADC calibration failed");

    for (int i = 0; i < ADC_SAMPLES; i++) {
        int raw = 0;
        int mv = 0;
        ESP_GOTO_ON_ERROR(adc_oneshot_read(adc, BOARD_VBAT_ADC_CHANNEL, &raw), out, TAG, "ADC read failed");
        ESP_GOTO_ON_ERROR(adc_cali_raw_to_voltage(cali, raw, &mv), out, TAG, "ADC convert failed");
        sum_mv += mv;
    }
    *millivolts = (uint16_t)(sum_mv * BOARD_VBAT_DIVIDER / ADC_SAMPLES);

out:
    gpio_set_level(BOARD_VBAT_EN_GPIO, 0);
    if (cali) {
        adc_cali_delete_scheme_curve_fitting(cali);
    }
    if (adc) {
        adc_oneshot_del_unit(adc);
    }
    return ret;
}

uint8_t battery_mv_to_half_percent(uint16_t millivolts)
{
    /* Open-circuit voltage of a LiPo cell at light load, piecewise linear. */
    static const struct {
        uint16_t mv;
        uint8_t percent;
    } curve[] = {
        {4150, 100}, {4050, 90}, {3970, 80}, {3900, 70}, {3840, 60}, {3790, 50},
        {3750, 40},  {3710, 30}, {3670, 20}, {3600, 10}, {3450, 5},  {3300, 0},
    };
    const size_t n = sizeof(curve) / sizeof(curve[0]);

    if (millivolts >= curve[0].mv) {
        return 200;
    }
    for (size_t i = 1; i < n; i++) {
        if (millivolts >= curve[i].mv) {
            const uint32_t span_mv = curve[i - 1].mv - curve[i].mv;
            const uint32_t span_half_pct = 2u * (curve[i - 1].percent - curve[i].percent);
            return (uint8_t)(2u * curve[i].percent + (millivolts - curve[i].mv) * span_half_pct / span_mv);
        }
    }
    return 0;
}
