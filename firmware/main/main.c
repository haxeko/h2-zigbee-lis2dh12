/*
 * Activity badge — ESP32-H2 + LIS2DH12, Zigbee sleepy end device.
 *
 * Division of labour:
 *   - The LIS2DH12 samples continuously at 1 Hz (low-power mode) into its
 *     32-level FIFO and raises INT1 when a batch is ready. It never sleeps.
 *   - The ESP32-H2 sleeps, wakes on INT1, drains the FIFO and accumulates
 *       a_mag = sqrt(ax² + ay² + az²),  a_dyn = |a_mag - 1 g|
 *     into sum_dyn / sample_count, which are kept in RTC memory.
 *   - Every CONFIG_BADGE_REPORT_SAMPLES samples (60 = one minute) it reports
 *       activity_score = sum_dyn / sample_count
 *     over Zigbee and starts a new window.
 *
 * Two sleep strategies share all of that logic (menuconfig: "Activity badge"):
 *   - deep sleep:  app_main() runs once per wake-up and ends in deep sleep.
 *   - light sleep: app_main() loops forever; FreeRTOS tickless idle puts the
 *                  chip in light sleep whenever the loop is blocked.
 */
#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_pm.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "soc/soc_caps.h"

#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
#include "driver/rtc_io.h"
#endif

#include "activity.h"
#include "battery.h"
#include "board.h"
#include "lis2dh12.h"
#include "zb_badge.h"

static const char *TAG = "badge";

/* Wake up two samples before the 32-level FIFO would start losing data. */
#define FIFO_BATCH_MAX       30
/* Back-up wake-up if INT1 never comes: batch time + sensor clock tolerance + margin. */
#define BATCH_TIMEOUT_S(n)   ((n) + (n) / 8 + 4)
/* Never let one window grow past a day (keeps sum_dyn far from overflow). */
#define WINDOW_MAX_SAMPLES   86400U
#define REPORT_TIMEOUT_MS    5000
#define SERVICE_POLL_MS      1000
#define LONG_PRESS_MS        5000
#define STATE_MAGIC          0xAC71B4D6U

typedef enum {
    SESSION_REPORT,    /* periodic report */
    SESSION_POWER_UP,  /* battery connected / reset: pairing allowed */
    SESSION_BUTTON,    /* short button press: pairing allowed, stay reachable */
} session_t;

/* Everything that must survive deep sleep. Zero after power-up. */
typedef struct {
    uint32_t magic;
    activity_acc_t acc;          /* sum_dyn and sample_count of the current window */
    uint32_t report_at;          /* report once acc.sample_count reaches this */
    uint16_t battery_countdown;  /* activity reports left until the next battery report */
    uint8_t fail_streak;         /* consecutive failed attempts, drives the back-off */
    bool commissioned;           /* the badge has joined a network */
} badge_state_t;

static RTC_DATA_ATTR badge_state_t s_state;

/* ------------------------------------------------------------------------- */
/* Board                                                                      */
/* ------------------------------------------------------------------------- */

static void board_init(void)
{
    const gpio_config_t outputs = {
        .pin_bit_mask = BIT64(BOARD_LED_GPIO) | BIT64(BOARD_VBAT_EN_GPIO),
        .mode = GPIO_MODE_OUTPUT,
    };
    const gpio_config_t button = {
        .pin_bit_mask = BIT64(BOARD_BUTTON_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    const gpio_config_t interrupts = {
        .pin_bit_mask = BIT64(BOARD_ACC_INT1_GPIO) | BIT64(BOARD_ACC_INT2_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE, /* defined level even if the sensor is missing */
    };

    gpio_set_level(BOARD_LED_GPIO, 0);
    gpio_set_level(BOARD_VBAT_EN_GPIO, 0);
    ESP_ERROR_CHECK(gpio_config(&outputs));
    ESP_ERROR_CHECK(gpio_config(&button));
    ESP_ERROR_CHECK(gpio_config(&interrupts));
}

static void led_set(bool on)
{
    gpio_set_level(BOARD_LED_GPIO, on);
}

static bool button_pressed(void)
{
    return gpio_get_level(BOARD_BUTTON_GPIO) == 0;
}

/* Call while the button is down. True if it stays down for LONG_PRESS_MS. */
static bool button_held_long(void)
{
    const int64_t start = esp_timer_get_time();

    while (button_pressed()) {
        if (esp_timer_get_time() - start >= LONG_PRESS_MS * 1000LL) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    return false;
}

static void factory_reset(void)
{
    ESP_LOGW(TAG, "long press: factory reset");
    for (int i = 0; i < 6; i++) {
        led_set(i % 2 == 0);
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    zb_badge_factory_reset(); /* does not return */
}

/* ------------------------------------------------------------------------- */
/* Sampling                                                                   */
/* ------------------------------------------------------------------------- */

/* Samples to collect before the next wake-up: up to the report, at most one FIFO batch. */
static uint8_t next_batch(void)
{
    const uint32_t missing = (s_state.report_at > s_state.acc.sample_count) ? s_state.report_at - s_state.acc.sample_count : 1;

    return (uint8_t)(missing > FIFO_BATCH_MAX ? FIFO_BATCH_MAX : missing);
}

/* (Re)start the sensor. Only at power-up or if it lost its configuration. */
static void sensor_start(void)
{
    lis2dh12_t acc;

    if (lis2dh12_open(&acc) != ESP_OK) {
        return;
    }
    if (lis2dh12_configure(&acc, next_batch()) == ESP_OK) {
        ESP_LOGI(TAG, "LIS2DH12 running: 1 Hz, low-power, FIFO stream");
    }
    lis2dh12_close(&acc);
}

/* Move every sample waiting in the sensor FIFO into the accumulator. */
static void sensor_drain(void)
{
    static lis2dh12_sample_t samples[LIS2DH12_FIFO_DEPTH];
    lis2dh12_t acc;
    size_t count = 0;
    bool configured = false;
    bool overrun = false;

    if (lis2dh12_open(&acc) != ESP_OK) {
        return;
    }
    if (lis2dh12_is_configured(&acc, &configured) == ESP_OK && !configured) {
        ESP_LOGW(TAG, "LIS2DH12 lost its configuration, restarting it");
        lis2dh12_configure(&acc, next_batch());
    } else if (lis2dh12_fifo_read(&acc, samples, &count, &overrun) == ESP_OK) {
        for (size_t i = 0; i < count; i++) {
            activity_add(&s_state.acc, samples[i].x_mg, samples[i].y_mg, samples[i].z_mg);
        }
        if (overrun) {
            ESP_LOGW(TAG, "FIFO overrun: samples older than 32 s were lost");
        }
    }
    lis2dh12_close(&acc);
    ESP_LOGI(TAG, "+%u samples, window: sum_dyn=%lu mg, count=%lu", (unsigned)count, (unsigned long)s_state.acc.sum_dyn_mg,
             (unsigned long)s_state.acc.sample_count);
}

/* Program the FIFO watermark so INT1 fires when the next batch is complete. */
static void sensor_arm(void)
{
    lis2dh12_t acc;

    if (lis2dh12_open(&acc) != ESP_OK) {
        return;
    }
    lis2dh12_set_watermark(&acc, next_batch());
    lis2dh12_close(&acc);
}

/* ------------------------------------------------------------------------- */
/* Reporting                                                                  */
/* ------------------------------------------------------------------------- */

static bool report_due(void)
{
    return s_state.acc.sample_count >= s_state.report_at;
}

static void window_restart(void)
{
    activity_reset(&s_state.acc);
    s_state.report_at = CONFIG_BADGE_REPORT_SAMPLES;
}

/* Network unreachable: keep accumulating and try again after 1, 2, 4, ... report periods. */
static void session_failed(void)
{
    if (s_state.fail_streak < 16) {
        s_state.fail_streak++;
    }
    uint32_t periods = 1UL << (s_state.fail_streak - 1);
    if (periods > CONFIG_BADGE_BACKOFF_MAX_PERIODS) {
        periods = CONFIG_BADGE_BACKOFF_MAX_PERIODS;
    }
    if (s_state.acc.sample_count >= WINDOW_MAX_SAMPLES) {
        activity_reset(&s_state.acc);
    }
    s_state.report_at = s_state.acc.sample_count + periods * CONFIG_BADGE_REPORT_SAMPLES;
    ESP_LOGW(TAG, "no network, next attempt in %lu report period(s)", (unsigned long)periods);
}

/* Keep the radio polling fast for a while so Zigbee2MQTT can interview/configure the badge. */
static void service_window(void)
{
    ESP_LOGI(TAG, "reachable for %d s (polling every %d ms)", CONFIG_BADGE_SERVICE_WINDOW_S, SERVICE_POLL_MS);
    led_set(true);
    zb_badge_set_poll_interval(SERVICE_POLL_MS);
    vTaskDelay(pdMS_TO_TICKS(CONFIG_BADGE_SERVICE_WINDOW_S * 1000));
    zb_badge_set_poll_interval(CONFIG_BADGE_ZB_POLL_INTERVAL_S * 1000U);
    led_set(false);
}

static void fill_battery(zb_badge_report_t *report)
{
#if CONFIG_BADGE_BATTERY_SENSE
    if (s_state.battery_countdown == 0 && battery_read_mv(&report->battery_mv) == ESP_OK) {
        report->battery_half_percent = battery_mv_to_half_percent(report->battery_mv);
        report->has_battery = true;
        ESP_LOGI(TAG, "battery %u mV (%u %%)", report->battery_mv, report->battery_half_percent / 2);
    }
#endif
}

/*
 * One radio session: get on the network, send what we have, update the state.
 * Pairing with a new network is only attempted at power-up or on a button
 * press, so an unpaired badge on a shelf does not burn its battery scanning.
 */
static void radio_session(session_t reason)
{
    zb_badge_report_t report = {0};
    const bool pairing_allowed = (reason != SESSION_REPORT);

    if (!s_state.commissioned && !pairing_allowed && !zb_badge_is_joined()) {
        window_restart(); /* nowhere to send to: drop the window */
        return;
    }

    /* Measure the battery before the radio loads it. */
    fill_battery(&report);

    if (pairing_allowed && !s_state.commissioned) {
        led_set(true);
    }
    const zb_net_status_t net =
        zb_badge_connect(s_state.commissioned ? CONFIG_BADGE_REJOIN_TIMEOUT_MS : CONFIG_BADGE_JOIN_WINDOW_S * 1000U);
    led_set(false);

    /* The stack knows from its stored data whether the badge was paired before (e.g. after a battery swap). */
    s_state.commissioned = zb_badge_is_commissioned();
    if (net == ZB_NET_UNAVAILABLE) {
        if (s_state.commissioned) {
            session_failed();
        } else {
            window_restart();
        }
        return;
    }

    if (net == ZB_NET_JOINED_NEW || reason == SESSION_BUTTON) {
        service_window();
    }

    report.has_activity = activity_score_g(&s_state.acc, &report.activity_score_g);
    if (report.has_activity) {
        ESP_LOGI(TAG, "activity_score = %lu mg / %lu samples = %d mg", (unsigned long)s_state.acc.sum_dyn_mg,
                 (unsigned long)s_state.acc.sample_count, (int)(report.activity_score_g * 1000.0f + 0.5f));
    }

    esp_err_t err = zb_badge_send_report(&report, REPORT_TIMEOUT_MS);
    if (err != ESP_OK && zb_badge_reconnect(CONFIG_BADGE_REJOIN_TIMEOUT_MS) != ZB_NET_UNAVAILABLE) {
        /* The wearer walks around: the parent router may be out of range. Retry once through a new one. */
        err = zb_badge_send_report(&report, REPORT_TIMEOUT_MS);
    }
    if (err != ESP_OK) {
        session_failed();
        return;
    }

    s_state.fail_streak = 0;
    if (report.has_activity) {
        window_restart();
    }
#if CONFIG_BADGE_BATTERY_SENSE
    if (report.has_battery) {
        s_state.battery_countdown = CONFIG_BADGE_BATTERY_REPORT_PERIOD;
    }
    if (report.has_activity && s_state.battery_countdown > 0) {
        s_state.battery_countdown--;
    }
#endif
}

/* ------------------------------------------------------------------------- */
/* Deep-sleep strategy                                                        */
/* ------------------------------------------------------------------------- */
#if CONFIG_BADGE_POWER_MODE_DEEP_SLEEP

typedef enum {
    WAKE_POWER_UP, /* not a deep-sleep wake-up: RTC memory is blank */
    WAKE_FIFO,     /* INT1: a batch of samples is ready */
    WAKE_TIMER,    /* back-up timer: INT1 did not come */
    WAKE_BUTTON,
} wake_t;

static wake_t wake_reason(void)
{
    if (esp_reset_reason() != ESP_RST_DEEPSLEEP || s_state.magic != STATE_MAGIC) {
        return WAKE_POWER_UP;
    }
    if (esp_sleep_get_wakeup_causes() & BIT(ESP_SLEEP_WAKEUP_EXT1)) {
        return (esp_sleep_get_ext1_wakeup_status() & BIT64(BOARD_BUTTON_GPIO)) ? WAKE_BUTTON : WAKE_FIFO;
    }
    return WAKE_TIMER;
}

static void enter_deep_sleep(void)
{
    const uint8_t batch = next_batch();

    ESP_ERROR_CHECK(esp_sleep_enable_timer_wakeup(BATCH_TIMEOUT_S((uint64_t)batch) * 1000000ULL));
    ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup_io(BIT64(BOARD_ACC_INT1_GPIO), ESP_EXT1_WAKEUP_ANY_HIGH));
    if (!button_pressed()) {
        /* Not armed while the button is still held, or the chip would wake up at once. */
        ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup_io(BIT64(BOARD_BUTTON_GPIO), ESP_EXT1_WAKEUP_ANY_LOW));
    }
#if SOC_RTCIO_INPUT_OUTPUT_SUPPORTED
    rtc_gpio_pullup_en(BOARD_BUTTON_GPIO);
    rtc_gpio_pulldown_dis(BOARD_BUTTON_GPIO);
    rtc_gpio_pulldown_en(BOARD_ACC_INT1_GPIO);
    rtc_gpio_pullup_dis(BOARD_ACC_INT1_GPIO);
#endif
    ESP_LOGI(TAG, "deep sleep until %u more samples are ready", batch);
    zb_badge_quiesce();
    esp_deep_sleep_start();
}

static void run(void)
{
    const wake_t wake = wake_reason();

    if (wake == WAKE_POWER_UP) {
        s_state = (badge_state_t){.magic = STATE_MAGIC, .report_at = CONFIG_BADGE_REPORT_SAMPLES};
        sensor_start();
        radio_session(SESSION_POWER_UP);
    } else {
        if (wake == WAKE_BUTTON && button_held_long()) {
            factory_reset();
        }
        sensor_drain();
        if (wake == WAKE_BUTTON) {
            radio_session(SESSION_BUTTON);
        } else if (report_due()) {
            radio_session(SESSION_REPORT);
        }
    }

    sensor_arm();
    enter_deep_sleep();
}

#endif /* CONFIG_BADGE_POWER_MODE_DEEP_SLEEP */

/* ------------------------------------------------------------------------- */
/* Light-sleep strategy                                                       */
/* ------------------------------------------------------------------------- */
#if CONFIG_BADGE_POWER_MODE_LIGHT_SLEEP

#define NOTIFY_FIFO   BIT0
#define NOTIFY_BUTTON BIT1

static TaskHandle_t s_main_task;

/* Level interrupts: mask the line until the task has dealt with its cause. */
static void gpio_isr(void *arg)
{
    const gpio_num_t gpio = (gpio_num_t)(uintptr_t)arg;
    BaseType_t woken = pdFALSE;

    gpio_intr_disable(gpio);
    xTaskNotifyFromISR(s_main_task, gpio == BOARD_BUTTON_GPIO ? NOTIFY_BUTTON : NOTIFY_FIFO, eSetBits, &woken);
    portYIELD_FROM_ISR(woken);
}

static void light_sleep_init(void)
{
    const esp_pm_config_t pm = {
        .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .min_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .light_sleep_enable = true,
    };

    s_main_task = xTaskGetCurrentTaskHandle();
    ESP_ERROR_CHECK(gpio_install_isr_service(0));
    ESP_ERROR_CHECK(gpio_set_intr_type(BOARD_ACC_INT1_GPIO, GPIO_INTR_HIGH_LEVEL));
    ESP_ERROR_CHECK(gpio_set_intr_type(BOARD_BUTTON_GPIO, GPIO_INTR_LOW_LEVEL));
    ESP_ERROR_CHECK(gpio_isr_handler_add(BOARD_ACC_INT1_GPIO, gpio_isr, (void *)(uintptr_t)BOARD_ACC_INT1_GPIO));
    ESP_ERROR_CHECK(gpio_isr_handler_add(BOARD_BUTTON_GPIO, gpio_isr, (void *)(uintptr_t)BOARD_BUTTON_GPIO));
    gpio_intr_disable(BOARD_ACC_INT1_GPIO);
    gpio_intr_disable(BOARD_BUTTON_GPIO);

    /*
     * Wake-up sources for automatic light sleep. EXT1 (LP GPIO) is the one that
     * works while the peripheral power domain is off; the GPIO wake-up covers
     * the case where that domain stays on.
     */
    ESP_ERROR_CHECK(gpio_wakeup_enable(BOARD_ACC_INT1_GPIO, GPIO_INTR_HIGH_LEVEL));
    ESP_ERROR_CHECK(gpio_wakeup_enable(BOARD_BUTTON_GPIO, GPIO_INTR_LOW_LEVEL));
    ESP_ERROR_CHECK(esp_sleep_enable_gpio_wakeup());
    ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup_io(BIT64(BOARD_ACC_INT1_GPIO), ESP_EXT1_WAKEUP_ANY_HIGH));
    ESP_ERROR_CHECK(esp_sleep_enable_ext1_wakeup_io(BIT64(BOARD_BUTTON_GPIO), ESP_EXT1_WAKEUP_ANY_LOW));

    ESP_ERROR_CHECK(esp_pm_configure(&pm));
}

static void run(void)
{
    s_state = (badge_state_t){.magic = STATE_MAGIC, .report_at = CONFIG_BADGE_REPORT_SAMPLES};
    light_sleep_init();
    sensor_start();
    radio_session(SESSION_POWER_UP);

    for (;;) {
        uint32_t events = 0;

        sensor_arm();
        gpio_intr_enable(BOARD_ACC_INT1_GPIO);
        if (!button_pressed()) {
            gpio_intr_enable(BOARD_BUTTON_GPIO);
        }
        /* Blocked here, the chip is in light sleep. The timeout is the back-up for a missed INT1. */
        xTaskNotifyWait(0, UINT32_MAX, &events, pdMS_TO_TICKS(BATCH_TIMEOUT_S(next_batch()) * 1000U));

        if ((events & NOTIFY_BUTTON) && button_held_long()) {
            factory_reset();
        }
        sensor_drain();
        if (events & NOTIFY_BUTTON) {
            radio_session(SESSION_BUTTON);
        } else if (report_due()) {
            radio_session(SESSION_REPORT);
        }
    }
}

#endif /* CONFIG_BADGE_POWER_MODE_LIGHT_SLEEP */

void app_main(void)
{
    board_init();
    run();
}
