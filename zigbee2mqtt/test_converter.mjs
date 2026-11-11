/*
 * Offline test of the external converter against the real zigbee-herdsman-converters
 * package: loads the definition the way Zigbee2MQTT does and feeds it fake reports.
 *
 *   cd zigbee2mqtt
 *   npm install --no-save zigbee-herdsman-converters
 *   node test_converter.mjs
 */
import * as zhc from 'zigbee-herdsman-converters';
import definition from './activity_badge.mjs';

const def = zhc.prepareDefinition(definition);
const device = {ieeeAddr: '0x0011223344556677', modelID: definition.zigbeeModel[0], endpoints: []};
const endpoint = {ID: 1, getDevice: () => device};

function convert(cluster, data) {
    const out = {};
    for (const fz of def.fromZigbee) {
        if (fz.cluster === cluster && fz.type.includes('attributeReport')) {
            const msg = {data, cluster, type: 'attributeReport', endpoint, device, linkquality: 120, groupID: 0, meta: {}};
            Object.assign(out, fz.convert(def, msg, () => {}, {}, {device, state: {}, options: {}}) ?? {});
        }
    }
    return out;
}

const activity = convert('genAnalogInput', {presentValue: 0.12741667});
const battery = convert('genPowerCfg', {batteryVoltage: 40, batteryPercentageRemaining: 172});

console.log('exposes:', def.exposes.map((e) => `${e.name}${e.unit ? ` [${e.unit}]` : ''}`).join(', '));
console.log('activity report ->', JSON.stringify(activity));
console.log('battery report  ->', JSON.stringify(battery));

const ok =
    activity.activity_score === 0.127 &&
    battery.battery === 86 &&
    battery.voltage === 4000 &&
    def.configure === undefined; // nothing may be sent to the sleeping badge at configure time
console.log(ok ? 'converter OK' : 'UNEXPECTED RESULT');
process.exit(ok ? 0 : 1);
