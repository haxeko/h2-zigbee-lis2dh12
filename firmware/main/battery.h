/* LiPo battery voltage measurement through the switched divider. */
#pragma once

#include <stdint.h>

#include "esp_err.h"

/**
 * Measure the battery voltage (takes ~10 ms, divider is powered only meanwhile).
 * Call it before the radio is started so the reading is not taken under TX load.
 */
esp_err_t battery_read_mv(uint16_t *millivolts);

/**
 * Rough state of charge of a single LiPo cell from its resting voltage,
 * in ZCL units of 0.5 % (0..200). 0 % is where the 3.3 V LDO drops out.
 */
uint8_t battery_mv_to_half_percent(uint16_t millivolts);
