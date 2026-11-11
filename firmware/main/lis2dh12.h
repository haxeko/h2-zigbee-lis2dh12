/*
 * Minimal LIS2DH12 driver for the activity badge.
 *
 * The sensor is the always-on part of the system: it samples at 1 Hz in
 * low-power mode (8-bit, ~2 µA) and buffers the samples in its 32-level FIFO
 * while the MCU sleeps. INT1 goes high when the FIFO holds the requested
 * number of samples (watermark), which wakes the MCU to read them in one burst.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "esp_err.h"

#define LIS2DH12_FIFO_DEPTH 32

typedef struct {
    i2c_master_bus_handle_t bus;
    i2c_master_dev_handle_t dev;
} lis2dh12_t;

/** One acceleration sample, in milli-g. */
typedef struct {
    int16_t x_mg;
    int16_t y_mg;
    int16_t z_mg;
} lis2dh12_sample_t;

/** Create the I2C bus and attach the sensor. Pair with lis2dh12_close(). */
esp_err_t lis2dh12_open(lis2dh12_t *acc);

/** Release the I2C bus (lets the peripheral power domain turn off in light sleep). */
void lis2dh12_close(lis2dh12_t *acc);

/**
 * Check that the sensor still holds the configuration written by
 * lis2dh12_configure() (it is lost if the sensor browns out).
 */
esp_err_t lis2dh12_is_configured(lis2dh12_t *acc, bool *configured);

/**
 * Full configuration: 1 Hz, low-power mode, ±2 g, FIFO in stream mode,
 * INT1 = FIFO watermark (active high). The FIFO is flushed.
 *
 * @param watermark INT1 asserts once this many samples are waiting (1..31).
 */
esp_err_t lis2dh12_configure(lis2dh12_t *acc, uint8_t watermark);

/** Change the FIFO watermark without disturbing sampling (1..31 samples). */
esp_err_t lis2dh12_set_watermark(lis2dh12_t *acc, uint8_t watermark);

/**
 * Read every sample waiting in the FIFO (oldest first).
 *
 * @param[out] out      Destination, at least LIS2DH12_FIFO_DEPTH entries.
 * @param[out] count    Number of samples read.
 * @param[out] overrun  True if the FIFO was full, i.e. older samples were lost.
 */
esp_err_t lis2dh12_fifo_read(lis2dh12_t *acc, lis2dh12_sample_t out[LIS2DH12_FIFO_DEPTH], size_t *count, bool *overrun);
