/*
 * Zigbee side of the activity badge (esp-zigbee-sdk 2.x, "ezbee" API).
 *
 * Sleepy End Device with one HA endpoint:
 *   Basic (0x0000), Power Configuration (0x0001), Identify (0x0003),
 *   Analog Input (0x000C) whose PresentValue carries activity_score in g.
 *
 * Reports are pushed explicitly with ZCL "Report Attributes" to the
 * coordinator (short address 0x0000, endpoint 1), so nothing has to be bound
 * or configured from Zigbee2MQTT for the data to flow.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define ZB_BADGE_ENDPOINT 1

typedef enum {
    ZB_NET_JOINED,      /* back on the network it was commissioned to */
    ZB_NET_JOINED_NEW,  /* just joined a network for the first time */
    ZB_NET_UNAVAILABLE, /* no network within the time allowed */
} zb_net_status_t;

typedef struct {
    bool has_activity;
    float activity_score_g;
    bool has_battery;
    uint16_t battery_mv;
    uint8_t battery_half_percent; /* 0..200, ZCL BatteryPercentageRemaining */
} zb_badge_report_t;

/**
 * Bring the network up and wait for the result.
 *
 * First call: starts the Zigbee stack, which rejoins the stored network or,
 * if the device is factory new, runs network steering (pairing).
 * Later calls: return at once when joined, otherwise retry the same procedure.
 *
 * @param timeout_ms  Give up (and stop retrying steering) after this long.
 */
zb_net_status_t zb_badge_connect(uint32_t timeout_ms);

/**
 * Rejoin the network even if the stack believes it is still on it. Used after
 * a failed transmission: the badge is worn by a person who walks around, so
 * its parent router may simply be out of range and another one has to be found.
 */
zb_net_status_t zb_badge_reconnect(uint32_t timeout_ms);

/** True while the device is on a network. */
bool zb_badge_is_joined(void);

/**
 * True if the badge has been paired with a network (even if that network is
 * out of reach right now). Valid once zb_badge_connect() has returned.
 */
bool zb_badge_is_commissioned(void);

/** Send the report and wait until every frame is confirmed (or timeout). */
esp_err_t zb_badge_send_report(const zb_badge_report_t *report, uint32_t timeout_ms);

/** MAC data poll period of the sleepy end device, in milliseconds. */
void zb_badge_set_poll_interval(uint32_t interval_ms);

/** Erase all Zigbee data and restart the chip: the badge is factory new again. */
void zb_badge_factory_reset(void);

/** Block the Zigbee task at a safe point; call right before deep sleep. */
void zb_badge_quiesce(void);
