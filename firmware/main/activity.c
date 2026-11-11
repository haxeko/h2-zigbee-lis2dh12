#include "activity.h"

#define ONE_G_MG 1000

/* Integer square root (floor), bit-by-bit; no libm, no soft-float. */
static uint32_t isqrt32(uint32_t v)
{
    uint32_t res = 0;
    uint32_t bit = 1UL << 30;

    while (bit > v) {
        bit >>= 2;
    }
    while (bit != 0) {
        if (v >= res + bit) {
            v -= res + bit;
            res = (res >> 1) + bit;
        } else {
            res >>= 1;
        }
        bit >>= 2;
    }
    return res;
}

uint16_t activity_dyn_mg(int16_t x_mg, int16_t y_mg, int16_t z_mg)
{
    /* Each axis is within ±32768 mg, so the sum of squares fits in 32 bits. */
    const uint32_t sq = (uint32_t)((int32_t)x_mg * x_mg) + (uint32_t)((int32_t)y_mg * y_mg) +
                        (uint32_t)((int32_t)z_mg * z_mg);
    const int32_t dyn = (int32_t)isqrt32(sq) - ONE_G_MG;

    return (uint16_t)(dyn < 0 ? -dyn : dyn);
}

void activity_add(activity_acc_t *acc, int16_t x_mg, int16_t y_mg, int16_t z_mg)
{
    acc->sum_dyn_mg += activity_dyn_mg(x_mg, y_mg, z_mg);
    acc->sample_count++;
}

bool activity_score_g(const activity_acc_t *acc, float *score_g)
{
    if (acc->sample_count == 0) {
        return false;
    }
    *score_g = (float)acc->sum_dyn_mg / (float)acc->sample_count / (float)ONE_G_MG;
    return true;
}

void activity_reset(activity_acc_t *acc)
{
    acc->sum_dyn_mg = 0;
    acc->sample_count = 0;
}
