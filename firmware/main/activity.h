/*
 * Activity-level accumulator.
 *
 *   a_mag   = sqrt(ax² + ay² + az²)
 *   a_dyn   = |a_mag - 1 g|
 *   score   = sum(a_dyn) / sample_count        (per reporting window)
 *
 * Everything is integer math in milli-g so the accumulator is a plain struct
 * that can live in RTC memory across deep sleep. No dependency on ESP-IDF:
 * the file also builds on a PC for unit tests (see firmware/test_host).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t sum_dyn_mg;   /* sum of a_dyn over the window, milli-g */
    uint32_t sample_count; /* number of samples in the window */
} activity_acc_t;

/** a_dyn of one sample, in milli-g. */
uint16_t activity_dyn_mg(int16_t x_mg, int16_t y_mg, int16_t z_mg);

/** Add one sample to the window. */
void activity_add(activity_acc_t *acc, int16_t x_mg, int16_t y_mg, int16_t z_mg);

/**
 * activity_score = sum_dyn / sample_count, in g.
 * @return false (score untouched) when the window holds no sample.
 */
bool activity_score_g(const activity_acc_t *acc, float *score_g);

/** Start a new window. */
void activity_reset(activity_acc_t *acc);
