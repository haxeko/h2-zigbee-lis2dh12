/*
 * Zigbee2MQTT external converter for the activity badge
 * (ESP32-H2 + LIS2DH12, firmware in ../firmware).
 *
 * Install: copy this file to <z2m data dir>/external_converters/ and restart
 * Zigbee2MQTT. Since Zigbee2MQTT 2.11 external converters only load when
 * `advanced: enable_external_js: true` is set in configuration.yaml.
 *
 * Published state, e.g. on zigbee2mqtt/<friendly_name>:
 *   {"activity_score": 0.127, "battery": 86, "voltage": 4000, "linkquality": 120}
 *
 * The badge pushes its reports by itself, so no binding or reporting
 * configuration is set up here (it sleeps and could miss those requests).
 */
import * as m from 'zigbee-herdsman-converters/lib/modernExtend';

export default {
    // Must match CONFIG_BADGE_ZB_MODEL / CONFIG_BADGE_ZB_MANUFACTURER in the firmware.
    zigbeeModel: ['AB-H2-LIS2DH12'],
    model: 'AB-H2-LIS2DH12',
    vendor: 'ActivityBadge',
    description: 'Wearable activity badge (ESP32-H2 + LIS2DH12)',
    extend: [
        m.numeric({
            name: 'activity_score',
            cluster: 'genAnalogInput', // 0x000C
            attribute: 'presentValue', // 0x0055, single-precision float
            description: 'Mean of |a_mag - 1 g| over the last reporting window (60 samples at 1 Hz)',
            unit: 'g',
            access: 'STATE', // push only: a sleeping badge cannot answer read requests
            precision: 3,
            reporting: false,
        }),
        m.battery({
            voltage: true, // genPowerCfg batteryVoltage (0x0020), published in mV
            percentageReporting: false, // genPowerCfg batteryPercentageRemaining (0x0021)
            voltageReporting: false,
        }),
    ],
};
