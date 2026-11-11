#include "zb_badge.h"

#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "esp_zigbee.h"
#include "ezbee/zha.h"

static const char *TAG = "zb_badge";

#define COORDINATOR_SHORT_ADDR 0x0000
#define COORDINATOR_ENDPOINT   1

#define ALL_CHANNELS_MASK      0x07FFF800U /* channels 11..26 */
#define STEERING_RETRY_MS      2000
#define ZB_TASK_STACK          4096
#define ZB_TASK_PRIORITY       5

#define EVT_JOINED       BIT0
#define EVT_UNAVAILABLE  BIT1
#define EVT_TX_DONE      BIT2

static EventGroupHandle_t s_events;
static esp_timer_handle_t s_retry_timer;
static bool s_started;
static volatile bool s_joined;
static volatile bool s_commissioned; /* network data stored: the badge has been paired */
static volatile bool s_new_join;
static volatile bool s_factory_reset;
static volatile int64_t s_deadline_us; /* no commissioning retries after this */
static volatile uintptr_t s_tx_batch; /* identifies the current zb_badge_send_report() call */
static volatile int s_tx_done;   /* report frames confirmed (delivered or not) */
static volatile int s_tx_failed; /* ... of which not delivered */

/* ------------------------------------------------------------------------- */
/* Commissioning                                                              */
/* ------------------------------------------------------------------------- */

static void network_up(void)
{
    s_joined = true;
    xEventGroupClearBits(s_events, EVT_UNAVAILABLE);
    xEventGroupSetBits(s_events, EVT_JOINED);
}

static void network_down(void)
{
    s_joined = false;
    xEventGroupClearBits(s_events, EVT_JOINED);
    xEventGroupSetBits(s_events, EVT_UNAVAILABLE);
}

/* Rejoin the stored network, or steer (pair) when there is none. Zigbee lock must be held. */
static void start_commissioning(void)
{
    const ezb_bdb_comm_mode_mask_t mode =
        ezb_bdb_is_factory_new() ? EZB_BDB_MODE_NETWORK_STEERING : EZB_BDB_MODE_INITIALIZATION;
    const ezb_err_t err = ezb_bdb_start_top_level_commissioning(mode);

    if (err != EZB_ERR_NONE) {
        ESP_LOGW(TAG, "commissioning (mode 0x%02x) not started: 0x%04x", mode, err);
        network_down();
    }
}

static void retry_timer_cb(void *arg)
{
    esp_zigbee_lock_acquire(portMAX_DELAY);
    start_commissioning();
    esp_zigbee_lock_release();
}

static bool signal_handler(const ezb_app_signal_t *app_signal)
{
    const ezb_app_signal_type_t type = ezb_app_signal_get_type(app_signal);

    switch (type) {
    case EZB_ZDO_SIGNAL_SKIP_STARTUP:
        if (s_factory_reset) {
            ESP_LOGW(TAG, "factory reset: erasing Zigbee data");
            esp_zigbee_factory_reset(); /* restarts the chip */
        }
        /* Loads the stored network (if any); answered by FIRST_START or REBOOT below. */
        ezb_bdb_start_top_level_commissioning(EZB_BDB_MODE_INITIALIZATION);
        break;

    case EZB_BDB_SIGNAL_DEVICE_FIRST_START:
    case EZB_BDB_SIGNAL_DEVICE_REBOOT: {
        const ezb_bdb_comm_status_t status = *(const ezb_bdb_comm_status_t *)ezb_app_signal_get_params(app_signal);
        s_commissioned = !ezb_bdb_is_factory_new();
        if (status != EZB_BDB_STATUS_SUCCESS) {
            /* Commissioned, but the network did not answer (out of range?). The app decides when to retry. */
            ESP_LOGW(TAG, "%s failed, status 0x%02x", ezb_app_signal_to_string(type), status);
            network_down();
        } else if (ezb_bdb_is_factory_new()) {
            ESP_LOGI(TAG, "factory new: start network steering");
            s_new_join = true;
            ezb_bdb_start_top_level_commissioning(EZB_BDB_MODE_NETWORK_STEERING);
        } else {
            ESP_LOGI(TAG, "rejoined PAN 0x%04hx, channel %d, short 0x%04hx", ezb_nwk_get_panid(),
                     ezb_nwk_get_current_channel(), ezb_nwk_get_short_address());
            network_up();
        }
    } break;

    case EZB_BDB_SIGNAL_STEERING: {
        const ezb_bdb_comm_status_t status = *(const ezb_bdb_comm_status_t *)ezb_app_signal_get_params(app_signal);
        if (status == EZB_BDB_STATUS_SUCCESS) {
            ESP_LOGI(TAG, "joined PAN 0x%04hx, channel %d, short 0x%04hx", ezb_nwk_get_panid(),
                     ezb_nwk_get_current_channel(), ezb_nwk_get_short_address());
            s_new_join = true;
            s_commissioned = true;
            network_up();
        } else if (esp_timer_get_time() < s_deadline_us) {
            ESP_LOGI(TAG, "no joinable network (status 0x%02x), retrying", status);
            esp_timer_start_once(s_retry_timer, STEERING_RETRY_MS * 1000ULL);
        } else {
            ESP_LOGW(TAG, "no joinable network, giving up");
            network_down();
        }
    } break;

    case EZB_ZDO_SIGNAL_LEAVE:
        /* Removed from the network by the coordinator: factory new from now on. */
        ESP_LOGW(TAG, "left the network");
        s_commissioned = false;
        network_down();
        break;

    case EZB_ZDO_SIGNAL_PARENT_LOST:
        /* Only seen when the stack stays up (light-sleep mode). zb_badge_connect() rejoins. */
        ESP_LOGW(TAG, "parent lost");
        network_down();
        break;

    default:
        ESP_LOGD(TAG, "signal %s (0x%02x)", ezb_app_signal_to_string(type), type);
        break;
    }
    return true;
}

/* ------------------------------------------------------------------------- */
/* Data model                                                                 */
/* ------------------------------------------------------------------------- */

/* ZCL character string: one length byte followed by the text. */
static void zcl_string(uint8_t *dst, size_t dst_size, const char *text)
{
    size_t len = strlen(text);

    if (len > dst_size - 1) {
        len = dst_size - 1;
    }
    dst[0] = (uint8_t)len;
    memcpy(&dst[1], text, len);
}

static esp_err_t create_data_model(void)
{
    static uint8_t manufacturer[33];
    static uint8_t model[33];
    uint8_t battery_voltage = 0xFF;    /* "unknown" until the first measurement */
    uint8_t battery_percentage = 0xFF; /* "unknown" */

    const ezb_af_ep_config_t ep_cfg = {
        .ep_id = ZB_BADGE_ENDPOINT,
        .app_profile_id = EZB_AF_HA_PROFILE_ID,
        .app_device_id = EZB_ZHA_SIMPLE_SENSOR_DEVICE_ID,
        .app_device_version = 0,
    };
    const ezb_zcl_basic_cluster_server_config_t basic_cfg = {
        .zcl_version = EZB_ZCL_BASIC_ZCL_VERSION_DEFAULT_VALUE,
        .power_source = EZB_ZCL_BASIC_POWER_SOURCE_BATTERY,
    };
    const ezb_zcl_identify_cluster_server_config_t identify_cfg = {
        .identify_time = EZB_ZCL_IDENTIFY_IDENTIFY_TIME_DEFAULT_VALUE,
    };
    const ezb_zcl_analog_input_cluster_server_config_t analog_cfg = {
        .out_of_service = false,
        .present_value = 0.0f,
        .status_flags = EZB_ZCL_ANALOG_INPUT_STATUS_FLAGS_DEFAULT_VALUE,
    };

    zcl_string(manufacturer, sizeof(manufacturer), CONFIG_BADGE_ZB_MANUFACTURER);
    zcl_string(model, sizeof(model), CONFIG_BADGE_ZB_MODEL);

    ezb_af_device_desc_t device = ezb_af_create_device_desc();
    ezb_af_ep_desc_t endpoint = ezb_af_create_endpoint_desc(&ep_cfg);
    ESP_RETURN_ON_FALSE(device && endpoint, ESP_ERR_NO_MEM, TAG, "descriptor allocation failed");

    ezb_zcl_cluster_desc_t basic = ezb_zcl_basic_create_cluster_desc(&basic_cfg, EZB_ZCL_CLUSTER_SERVER);
    ezb_zcl_basic_cluster_desc_add_attr(basic, EZB_ZCL_ATTR_BASIC_MANUFACTURER_NAME_ID, manufacturer);
    ezb_zcl_basic_cluster_desc_add_attr(basic, EZB_ZCL_ATTR_BASIC_MODEL_IDENTIFIER_ID, model);

    ezb_zcl_cluster_desc_t power = ezb_zcl_power_config_create_cluster_desc(NULL, EZB_ZCL_CLUSTER_SERVER);
    ezb_zcl_power_config_cluster_desc_add_attr(power, EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_VOLTAGE_ID, &battery_voltage);
    ezb_zcl_power_config_cluster_desc_add_attr(power, EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_PERCENTAGE_REMAINING_ID,
                                               &battery_percentage);

    ezb_zcl_cluster_desc_t identify = ezb_zcl_identify_create_cluster_desc(&identify_cfg, EZB_ZCL_CLUSTER_SERVER);
    ezb_zcl_cluster_desc_t analog = ezb_zcl_analog_input_create_cluster_desc(&analog_cfg, EZB_ZCL_CLUSTER_SERVER);

    ESP_RETURN_ON_FALSE(basic && power && identify && analog, ESP_ERR_NO_MEM, TAG, "cluster allocation failed");
    ESP_RETURN_ON_ERROR(esp_zigbee_err_to_esp(ezb_af_endpoint_add_cluster_desc(endpoint, basic)), TAG, "basic");
    ESP_RETURN_ON_ERROR(esp_zigbee_err_to_esp(ezb_af_endpoint_add_cluster_desc(endpoint, power)), TAG, "power");
    ESP_RETURN_ON_ERROR(esp_zigbee_err_to_esp(ezb_af_endpoint_add_cluster_desc(endpoint, identify)), TAG, "identify");
    ESP_RETURN_ON_ERROR(esp_zigbee_err_to_esp(ezb_af_endpoint_add_cluster_desc(endpoint, analog)), TAG, "analog");
    ESP_RETURN_ON_ERROR(esp_zigbee_err_to_esp(ezb_af_device_add_endpoint_desc(device, endpoint)), TAG, "endpoint");
    ESP_RETURN_ON_ERROR(esp_zigbee_err_to_esp(ezb_af_device_desc_register(device)), TAG, "device");
    return ESP_OK;
}

/* ------------------------------------------------------------------------- */
/* Stack task                                                                 */
/* ------------------------------------------------------------------------- */

static void zigbee_task(void *arg)
{
    const esp_zigbee_config_t config = {
        .device_config =
            {
                .device_type = EZB_NWK_DEVICE_TYPE_END_DEVICE,
                .install_code_policy = false,
                .zed_config =
                    {
                        /* The parent forgets the badge if it stays silent longer than this. */
                        .ed_timeout = EZB_NWK_ED_TIMEOUT_64MIN,
                        .keep_alive = CONFIG_BADGE_ZB_POLL_INTERVAL_S * 1000U,
                    },
            },
        .platform_config =
            {
                .storage_partition_name = "nvs",
                .radio_config = {.radio_mode = ESP_ZIGBEE_RADIO_MODE_NATIVE},
            },
    };

    ESP_ERROR_CHECK(esp_zigbee_init(&config));

    /* Zigbee2MQTT runs a centralized (Trust Center) network. */
    ezb_aps_secur_enable_distributed_security(false);
    ezb_bdb_set_primary_channel_set(CONFIG_BADGE_ZB_CHANNEL_MASK);
    ezb_bdb_set_secondary_channel_set(ALL_CHANNELS_MASK & ~(uint32_t)CONFIG_BADGE_ZB_CHANNEL_MASK);
    ezb_nwk_set_rx_on_when_idle(false); /* sleepy end device */
    ezb_nwk_set_tx_power(CONFIG_BADGE_ZB_TX_POWER_DBM);
    ESP_ERROR_CHECK(esp_zigbee_err_to_esp(ezb_app_signal_add_handler(signal_handler)));
    ESP_ERROR_CHECK(create_data_model());

    ESP_ERROR_CHECK(esp_zigbee_start(false));
    esp_zigbee_launch_mainloop();

    esp_zigbee_deinit();
    vTaskDelete(NULL);
}

static esp_err_t start_stack(void)
{
    const esp_timer_create_args_t timer_args = {.callback = retry_timer_cb, .name = "zb_retry"};

    /*
     * The stack keeps its network data in NVS. Initialised here rather than in
     * app_main() so that wake-ups that only drain the sensor FIFO skip it.
     */
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "NVS erase");
        err = nvs_flash_init();
    }
    ESP_RETURN_ON_ERROR(err, TAG, "NVS init");

    s_events = xEventGroupCreate();
    ESP_RETURN_ON_FALSE(s_events, ESP_ERR_NO_MEM, TAG, "event group");
    ESP_RETURN_ON_ERROR(esp_timer_create(&timer_args, &s_retry_timer), TAG, "retry timer");
    ESP_RETURN_ON_FALSE(xTaskCreate(zigbee_task, "zigbee", ZB_TASK_STACK, NULL, ZB_TASK_PRIORITY, NULL) == pdPASS,
                        ESP_ERR_NO_MEM, TAG, "zigbee task");
    s_started = true;
    return ESP_OK;
}

/* ------------------------------------------------------------------------- */
/* Public API                                                                 */
/* ------------------------------------------------------------------------- */

zb_net_status_t zb_badge_connect(uint32_t timeout_ms)
{
    s_deadline_us = esp_timer_get_time() + (int64_t)timeout_ms * 1000;

    if (!s_started) {
        if (start_stack() != ESP_OK) {
            return ZB_NET_UNAVAILABLE;
        }
    } else if (!s_joined) {
        xEventGroupClearBits(s_events, EVT_UNAVAILABLE);
        esp_zigbee_lock_acquire(portMAX_DELAY);
        start_commissioning();
        esp_zigbee_lock_release();
    }

    /* A steering attempt that is already running may finish slightly after the deadline. */
    const EventBits_t bits =
        xEventGroupWaitBits(s_events, EVT_JOINED | EVT_UNAVAILABLE, pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms + 5000));
    if (!(bits & EVT_JOINED)) {
        return ZB_NET_UNAVAILABLE;
    }
    if (s_new_join) {
        s_new_join = false;
        return ZB_NET_JOINED_NEW;
    }
    return ZB_NET_JOINED;
}

zb_net_status_t zb_badge_reconnect(uint32_t timeout_ms)
{
    if (s_started) {
        s_joined = false;
        xEventGroupClearBits(s_events, EVT_JOINED);
    }
    return zb_badge_connect(timeout_ms);
}

bool zb_badge_is_joined(void)
{
    return s_started && s_joined;
}

bool zb_badge_is_commissioned(void)
{
    return s_started && s_commissioned;
}

static void report_confirm_cb(ezb_zcl_cmd_cnf_t *cnf, void *user_ctx)
{
    if ((uintptr_t)user_ctx != s_tx_batch) {
        return; /* late confirmation of a report we already gave up on */
    }
    if (cnf->status != 0) {
        ESP_LOGW(TAG, "report on cluster 0x%04x not delivered, status 0x%02x", cnf->cluster_id, cnf->status);
        s_tx_failed++;
    }
    s_tx_done++;
    xEventGroupSetBits(s_events, EVT_TX_DONE);
}

/* Queue one Report Attributes frame. Zigbee lock must be held. Returns the number of frames queued (0 or 1). */
static int report_attribute(uint16_t cluster_id, uint16_t attr_id)
{
    const ezb_zcl_report_attr_cmd_t cmd = {
        .cmd_ctrl =
            {
                .dst_addr = {.addr_mode = EZB_ADDR_MODE_SHORT, .u.short_addr = COORDINATOR_SHORT_ADDR},
                .dst_ep = COORDINATOR_ENDPOINT,
                .src_ep = ZB_BADGE_ENDPOINT,
                .cluster_id = cluster_id,
                .manuf_code = EZB_ZCL_STD_MANUF_CODE,
                .fc = {.direction = EZB_ZCL_CMD_DIRECTION_TO_CLI, .dis_default_rsp = 1},
                .cnf_ctx = {.cb = report_confirm_cb, .user_ctx = (void *)s_tx_batch},
            },
        .payload = {.attr_id = attr_id},
    };

    const ezb_err_t err = ezb_zcl_report_attr_cmd_req(&cmd);
    if (err != EZB_ERR_NONE) {
        ESP_LOGW(TAG, "report request rejected: 0x%04x", err);
        s_tx_failed++;
        return 0;
    }
    return 1;
}

static void set_attribute(uint16_t cluster_id, uint16_t attr_id, void *value)
{
    const ezb_zcl_status_t status = ezb_zcl_set_attr_value(ZB_BADGE_ENDPOINT, cluster_id, EZB_ZCL_CLUSTER_SERVER, attr_id,
                                                           EZB_ZCL_STD_MANUF_CODE, value, false);
    if (status != EZB_ZCL_STATUS_SUCCESS) {
        ESP_LOGW(TAG, "set attribute 0x%04x/0x%04x failed: 0x%02x", cluster_id, attr_id, status);
    }
}

esp_err_t zb_badge_send_report(const zb_badge_report_t *report, uint32_t timeout_ms)
{
    ESP_RETURN_ON_FALSE(zb_badge_is_joined(), ESP_ERR_INVALID_STATE, TAG, "not joined");
    if (!report->has_activity && !report->has_battery) {
        return ESP_OK;
    }

    int queued = 0;

    xEventGroupClearBits(s_events, EVT_TX_DONE);
    esp_zigbee_lock_acquire(portMAX_DELAY);
    s_tx_batch++;
    s_tx_done = 0;
    s_tx_failed = 0;
    if (report->has_activity) {
        float score = report->activity_score_g;
        set_attribute(EZB_ZCL_CLUSTER_ID_ANALOG_INPUT, EZB_ZCL_ATTR_ANALOG_INPUT_PRESENT_VALUE_ID, &score);
        queued += report_attribute(EZB_ZCL_CLUSTER_ID_ANALOG_INPUT, EZB_ZCL_ATTR_ANALOG_INPUT_PRESENT_VALUE_ID);
    }
    if (report->has_battery) {
        uint8_t voltage = (uint8_t)((report->battery_mv + 50) / 100); /* ZCL unit: 100 mV */
        uint8_t percentage = report->battery_half_percent;
        set_attribute(EZB_ZCL_CLUSTER_ID_POWER_CONFIG, EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_VOLTAGE_ID, &voltage);
        set_attribute(EZB_ZCL_CLUSTER_ID_POWER_CONFIG, EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_PERCENTAGE_REMAINING_ID,
                      &percentage);
        queued += report_attribute(EZB_ZCL_CLUSTER_ID_POWER_CONFIG, EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_VOLTAGE_ID);
        queued += report_attribute(EZB_ZCL_CLUSTER_ID_POWER_CONFIG,
                                   EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_PERCENTAGE_REMAINING_ID);
    }
    esp_zigbee_lock_release();

    /* Every queued frame is confirmed once by report_confirm_cb(), possibly before we get here. */
    const int64_t deadline_us = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    while (s_tx_done < queued) {
        const int64_t left_us = deadline_us - esp_timer_get_time();
        ESP_RETURN_ON_FALSE(left_us > 0, ESP_ERR_TIMEOUT, TAG, "report confirmation timed out");
        xEventGroupWaitBits(s_events, EVT_TX_DONE, pdTRUE, pdFALSE, pdMS_TO_TICKS(left_us / 1000) + 1);
    }
    return s_tx_failed == 0 ? ESP_OK : ESP_FAIL;
}

void zb_badge_set_poll_interval(uint32_t interval_ms)
{
    if (!s_started) {
        return;
    }
    esp_zigbee_lock_acquire(portMAX_DELAY);
    ezb_nwk_set_keepalive_interval(interval_ms);
    esp_zigbee_lock_release();
}

void zb_badge_factory_reset(void)
{
    s_factory_reset = true;
    if (!s_started) {
        /* The reset is carried out as soon as the stack storage is up (SKIP_STARTUP). */
        if (start_stack() != ESP_OK) {
            return;
        }
        vTaskDelay(portMAX_DELAY);
    }
    esp_zigbee_lock_acquire(portMAX_DELAY);
    esp_zigbee_factory_reset(); /* restarts the chip */
}

void zb_badge_quiesce(void)
{
    if (s_started) {
        esp_zigbee_lock_acquire(portMAX_DELAY);
    }
}
