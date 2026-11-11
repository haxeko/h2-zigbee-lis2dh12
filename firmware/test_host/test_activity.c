/*
 * Host unit test for the activity algorithm (no ESP-IDF needed):
 *   cc -Wall -Wextra -Werror -I../main test_activity.c ../main/activity.c -lm -o test_activity && ./test_activity
 */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "activity.h"

static void test_dyn(void)
{
    /* At rest in any orientation a_mag = 1 g, so a_dyn = 0. */
    assert(activity_dyn_mg(0, 0, 1000) == 0);
    assert(activity_dyn_mg(0, -1000, 0) == 0);
    assert(activity_dyn_mg(600, 0, 800) == 0);
    /* Free fall: a_mag = 0 -> a_dyn = 1 g. */
    assert(activity_dyn_mg(0, 0, 0) == 1000);
    /* sqrt(3) g = 1732 mg -> 732 mg. */
    assert(activity_dyn_mg(1000, 1000, 1000) == 732);
    assert(activity_dyn_mg(-1000, 1000, -1000) == 732);
    /* Full scale of the 8-bit ±2 g data on all axes, and the int16 extremes. */
    assert(activity_dyn_mg(2032, 2032, 2032) == 2519);
    assert(activity_dyn_mg(-32768, -32768, -32768) == 55755);
}

/* Compare with the floating-point formula over random inputs. */
static void test_against_float(void)
{
    srand(1);
    for (int i = 0; i < 200000; i++) {
        const int x = (rand() % 8001) - 4000;
        const int y = (rand() % 8001) - 4000;
        const int z = (rand() % 8001) - 4000;
        const double mag = sqrt((double)x * x + (double)y * y + (double)z * z);
        const double expected = fabs(floor(mag) - 1000.0); /* integer sqrt rounds down */
        assert(activity_dyn_mg((int16_t)x, (int16_t)y, (int16_t)z) == (uint16_t)expected);
    }
}

static void test_window(void)
{
    activity_acc_t acc = {0};
    float score = -1.0f;

    /* No sample: no score. */
    assert(!activity_score_g(&acc, &score));
    assert(score == -1.0f);

    /* 60 s: 30 samples at rest, 30 samples with a_mag = 1.2 g. */
    for (int i = 0; i < 30; i++) {
        activity_add(&acc, 0, 0, 1000);
        activity_add(&acc, 0, 0, 1200);
    }
    assert(acc.sample_count == 60);
    assert(acc.sum_dyn_mg == 30 * 200);
    assert(activity_score_g(&acc, &score));
    assert(fabsf(score - 0.100f) < 1e-6f);

    activity_reset(&acc);
    assert(acc.sample_count == 0 && acc.sum_dyn_mg == 0);
    assert(!activity_score_g(&acc, &score));
}

int main(void)
{
    test_dyn();
    test_against_float();
    test_window();
    puts("activity: all tests passed");
    return 0;
}
