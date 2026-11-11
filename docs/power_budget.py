#!/usr/bin/env python3
"""
Battery-life estimate for the activity badge.

Every number below is either a datasheet "typical" or an assumption (marked
ASSUMED) that has to be replaced by a measurement on real hardware (Nordic
PPK2, Joulescope, Otii ...). Run:  python3 docs/power_budget.py
"""

# --- Sleep floor (µA) --------------------------------------------------------
I_H2_DEEP_SLEEP = 7.0    # ESP32-H2 datasheet: LP timer + LP memory on
I_H2_LIGHT_SLEEP = 25.0  # ESP32-H2 datasheet: CPU, radio and peripherals powered down (85 if peripherals stay on)
I_LIS2DH12 = 2.0         # LIS2DH12 datasheet: 1 Hz, low-power mode
I_LDO_Q = 2.0            # RT9080 quiescent current
I_LEAKAGE = 1.0          # ASSUMED: charger reverse leakage, ESD diodes, capacitors

# --- Active currents (mA) ----------------------------------------------------
I_CPU = 10.0             # ESP32-H2 datasheet, modem-sleep, 96 MHz, CPU running (10..17)
I_RX = 26.0              # 802.15.4 receive
I_TX = 60.0              # 802.15.4 transmit at 9..10 dBm (36 at 0 dBm, 140 at 20 dBm)

# --- Battery -----------------------------------------------------------------
BATTERY_MAH = 500.0
USABLE_FRACTION = 0.85   # ASSUMED: capacity above the ~3.4 V where the 3.3 V LDO drops out
I_SELF_DISCHARGE = 15.0  # µA, ASSUMED: ~2 %/month cell self-discharge + protection circuit

FIFO_SERVICE_PER_MIN = 2.0  # the 32-sample FIFO at 1 Hz must be read every <= 32 s

# --- Cost of one event (mC). The times are ASSUMED until measured. -----------


def mc(current_ma, time_ms):
    """Charge in millicoulomb of a current pulse."""
    return current_ma * time_ms / 1000.0


# Deep sleep: every wake-up is a full boot.
Q_DEEP_FIFO_WAKE = mc(I_CPU, 80)                      # ROM + bootloader + app start + I2C burst, logging off
Q_DEEP_REPORT_BOOT = mc(I_CPU, 70) + mc(I_CPU + 2, 150)  # boot + Zigbee stack init
Q_DEEP_REJOIN = mc(I_RX + 4, 300)                     # receiver on for the rejoin (plausible range 100 ms .. 1 s)
Q_DEEP_REPORT_TX = mc(I_TX, 4) + mc(I_RX + 4, 50)     # report + wait for confirmation

# Light sleep: RAM and the Zigbee stack are retained.
Q_LIGHT_FIFO_WAKE = mc(8, 10)                         # wake from light sleep + I2C burst
Q_LIGHT_REPORT = mc(I_CPU, 20) + mc(I_TX, 4) + mc(I_RX, 17)  # CPU + TX + MAC ack + poll for the APS ack
Q_LIGHT_BATTERY = 2 * (mc(I_TX, 4) + mc(I_RX, 17)) / 10      # 2 frames every 10th report
Q_LIGHT_KEEPALIVE_PER_MIN = mc(I_RX + 4, 10) / 5             # one parent poll every 5 min


def deep_sleep_mode(period_min):
    """List of (label, mC per minute) for a report every period_min minutes."""
    floor = I_H2_DEEP_SLEEP + I_LIS2DH12 + I_LDO_Q + I_LEAKAGE
    fifo_only_wakes = FIFO_SERVICE_PER_MIN - 1.0 / period_min  # the report wake-up also drains the FIFO
    return [
        ("sleep floor (%.0f uA)" % floor, floor * 1e-3 * 60),
        ("FIFO-only wake-ups (boot + I2C)", fifo_only_wakes * Q_DEEP_FIFO_WAKE),
        ("report: boot + Zigbee stack init", Q_DEEP_REPORT_BOOT / period_min),
        ("report: rejoin (scan, rejoin request/response)", Q_DEEP_REJOIN / period_min),
        ("report: TX + wait for confirmation", Q_DEEP_REPORT_TX / period_min),
    ]


def light_sleep_mode(period_min):
    floor = I_H2_LIGHT_SLEEP + I_LIS2DH12 + I_LDO_Q + I_LEAKAGE
    return [
        ("sleep floor (%.0f uA)" % floor, floor * 1e-3 * 60),
        ("FIFO drains (wake from light sleep + I2C)", FIFO_SERVICE_PER_MIN * Q_LIGHT_FIFO_WAKE),
        ("report: CPU + TX + MAC ack + poll for APS ack", Q_LIGHT_REPORT / period_min),
        ("battery report every 10th report, averaged", Q_LIGHT_BATTERY / period_min),
        ("keep-alive poll every 5 min, averaged", Q_LIGHT_KEEPALIVE_PER_MIN),
    ]


def average_ua(items):
    return sum(q for _, q in items) / 60.0 * 1000.0


def days(avg_ua):
    return BATTERY_MAH * USABLE_FRACTION / ((avg_ua + I_SELF_DISCHARGE) / 1000.0) / 24.0


def show(name, items):
    print(name)
    for label, q in items:
        print("  %-50s %6.2f mC/min" % (label, q))
    print("  %-50s %6.2f mC/min" % ("TOTAL", sum(q for _, q in items)))
    avg = average_ua(items)
    print("  average current: %.0f uA  ->  %.0f days on %d mAh (with %d uA self-discharge)\n"
          % (avg, days(avg), BATTERY_MAH, I_SELF_DISCHARGE))


if __name__ == "__main__":
    show("Deep sleep, report every minute (CONFIG_BADGE_POWER_MODE_DEEP_SLEEP)", deep_sleep_mode(1))
    show("Light sleep, report every minute (CONFIG_BADGE_POWER_MODE_LIGHT_SLEEP)", light_sleep_mode(1))

    print("Report period    deep sleep           light sleep")
    for period in (1, 2, 5, 15, 60):
        d = average_ua(deep_sleep_mode(period))
        l = average_ua(light_sleep_mode(period))
        print("  %3d min      %4.0f uA (%3.0f days)   %4.0f uA (%3.0f days)" % (period, d, days(d), l, days(l)))
