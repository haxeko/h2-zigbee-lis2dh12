# Zigbee2MQTT: mô hình dữ liệu, ghép nối và kiểm thử

## 1. Mô hình dữ liệu Zigbee

Badge là **End Device, RxOnWhenIdle = false (Sleepy)**, một endpoint:

| Mục | Giá trị |
|---|---|
| Endpoint | 1 |
| Profile | Home Automation `0x0104` |
| Device ID | Simple Sensor `0x000C` |
| Basic `0x0000` | ManufacturerName = `ActivityBadge`, ModelIdentifier = `AB-H2-LIS2DH12`, PowerSource = Battery (0x03) |
| Identify `0x0003` | bắt buộc theo HA |
| Power Configuration `0x0001` | BatteryVoltage `0x0020` (uint8, đơn vị 100 mV), BatteryPercentageRemaining `0x0021` (uint8, đơn vị 0,5 %) |
| **Analog Input (Basic) `0x000C`** | **PresentValue `0x0055` (single float) = `activity_score` tính bằng g** |

### Vì sao chọn Analog Input / PresentValue

- Là cluster **chuẩn ZCL** dành cho "một giá trị đo kiểu số thực không thuộc loại cảm biến nào có
  sẵn". Zigbee2MQTT (`genAnalogInput`), ZHA và các công cụ sniff đều hiểu sẵn kiểu dữ liệu.
- Không cần manufacturer code, không cần định nghĩa cluster riêng ở phía Z2M: converter chỉ là một
  dòng `m.numeric(...)`.
- Kiểu `single` giữ nguyên công thức của đề bài (`sum_dyn / sample_count`, đơn vị g) mà không phải
  quy ước hệ số nhân.

Phương án thay thế khi cần gửi nhiều trường hơn (ví dụ `sample_count`, đỉnh gia tốc, số bước):
cluster riêng của nhà sản xuất (`0xFC00`–`0xFFFF`) với các attribute tự định nghĩa, khai báo phía
Z2M bằng `m.deviceAddCustomCluster(...)`. Phức tạp hơn ở cả hai đầu nên không dùng khi chỉ có một
giá trị.

### Cách gửi

Firmware **chủ động gửi lệnh ZCL Report Attributes (0x0A)** tới coordinator (địa chỉ ngắn `0x0000`,
endpoint 1) ngay sau khi vào mạng, bằng `ezb_zcl_report_attr_cmd_req()`:

- mỗi phút: `genAnalogInput.presentValue`;
- mỗi 10 báo cáo (và khi cấp nguồn): `genPowerCfg.batteryVoltage` và `batteryPercentageRemaining`.

Không dùng cơ chế "Configure Reporting + binding" vì thiết bị ngủ gần như suốt thời gian: nó dễ bỏ lỡ
các lệnh cấu hình của Z2M, và ở chế độ deep sleep bộ đếm thời gian reporting của stack bị xoá sau
mỗi lần khởi động lại. Gửi thẳng thì không phụ thuộc vào bước cấu hình nào từ Z2M.

## 2. Converter cho Zigbee2MQTT

File [`zigbee2mqtt/activity_badge.mjs`](../zigbee2mqtt/activity_badge.mjs):

```js
import * as m from 'zigbee-herdsman-converters/lib/modernExtend';

export default {
    zigbeeModel: ['AB-H2-LIS2DH12'],
    model: 'AB-H2-LIS2DH12',
    vendor: 'ActivityBadge',
    description: 'Wearable activity badge (ESP32-H2 + LIS2DH12)',
    extend: [
        m.numeric({
            name: 'activity_score',
            cluster: 'genAnalogInput',
            attribute: 'presentValue',
            description: 'Mean of |a_mag - 1 g| over the last reporting window (60 samples at 1 Hz)',
            unit: 'g',
            access: 'STATE',
            precision: 3,
            reporting: false,
        }),
        m.battery({voltage: true, percentageReporting: false, voltageReporting: false}),
    ],
};
```

`access: 'STATE'` và `reporting: false` làm cho bước *configure* của Z2M không gửi bất kỳ yêu cầu
nào tới badge (đã kiểm tra: `definition.configure === undefined`).

Cài đặt:

1. Chép file vào thư mục `external_converters/` nằm cạnh `configuration.yaml` của Z2M.
2. Từ Zigbee2MQTT 2.11, external converter chỉ được nạp khi bật trong `configuration.yaml`:

   ```yaml
   advanced:
     enable_external_js: true
   ```
3. Khởi động lại Zigbee2MQTT.

Converter đã được chạy thử với thư viện thật (zigbee-herdsman-converters 26.129.0) bằng báo cáo giả lập:

```bash
cd zigbee2mqtt && npm install --no-save zigbee-herdsman-converters && node test_converter.mjs
```

Kết quả:

```
exposes: activity_score [g], battery [%], voltage [mV]
activity report -> {"activity_score":0.127}
battery report  -> {"battery":86,"voltage":4000}
converter OK
```

## 3. Ghép nối (join)

1. Trong Z2M bật **Permit join** (giao diện web hoặc `zigbee2mqtt/bridge/request/permit_join`).
2. Cấp nguồn cho badge (nối pin hoặc nhấn RESET) hoặc **nhấn nút BOOT ngắn**. LED sáng trong lúc badge tìm mạng
   (tối đa 180 s, quét kênh 11–26).
3. Khi vào mạng, badge **thức thêm 45 s và poll parent mỗi giây** để Z2M đọc node descriptor,
   endpoint, Basic cluster (interview). Hết 45 s nó gửi báo cáo đầu tiên rồi vào chu kỳ ngủ.
4. Trong Z2M thiết bị hiện ra với model `AB-H2-LIS2DH12`, có các trường `activity_score`,
   `battery`, `voltage`.

Mẹo:

- Biết trước kênh của mạng thì đặt `CONFIG_BADGE_ZB_CHANNEL_MASK` chỉ một kênh (ví dụ kênh 11 =
  `0x800`): ghép nối nhanh hơn và tốn ít pin hơn nhiều so với quét 16 kênh.
- Interview chưa xong mà badge đã ngủ: nhấn nút ngắn (thức 45 s) rồi bấm *Reconfigure/Interview*
  trong Z2M.
- Ghép nối lại từ đầu: giữ nút ≥ 5 s (factory reset), xoá thiết bị trong Z2M (*Force remove*).

## 4. Kiểm thử với Zigbee2MQTT

Theo dõi MQTT:

```bash
mosquitto_sub -h <broker> -t 'zigbee2mqtt/#' -v
```

Mỗi ~60 s phải có một bản tin dạng:

```
zigbee2mqtt/badge_01 {"activity_score":0.127,"battery":86,"voltage":4000,"linkquality":120}
```

Danh sách kiểm tra:

| Bước | Kỳ vọng |
|---|---|
| Badge nằm yên trên bàn 3 phút | `activity_score` ≈ 0,00–0,03 g |
| Lắc/đi bộ với badge trong 1 phút | `activity_score` tăng rõ (đi bộ ~0,1–0,3 g) |
| Khoảng cách giữa hai bản tin | 60 s ± vài % (đồng hồ của cảm biến) |
| Bản tin đầu sau khi cấp nguồn và mỗi ~10 phút | có `battery`, `voltage` |
| Tắt coordinator 5 phút rồi bật lại | badge thử lại với khoảng giãn 1, 2, 4 phút…; bản tin đầu tiên sau đó là trung bình của cả khoảng mất mạng |
| Mang badge đi xa router đang làm parent, lại gần router khác | báo cáo vẫn đều (badge rejoin qua router mới) |

Phát hiện badge mất liên lạc bằng tính năng availability của Z2M (thiết bị dùng pin là loại
*passive*, mặc định 25 giờ mới báo offline; nên hạ xuống vài phút cho badge):

```yaml
availability:
  enabled: true
devices:
  '0x0011223344556677':
    friendly_name: badge_01
    availability:
      timeout: 5   # phút
```

Gỡ lỗi sóng: sniff kênh Zigbee (nRF52840 dongle + Wireshark, khoá mạng lấy từ Z2M) để xem trực
tiếp khung Rejoin Request/Response và Report Attributes của badge.

## 5. Quy hoạch mạng cho công trường

- Mỗi badge là một *child* của một router (hoặc của coordinator). Bảng child của coordinator và
  router có giới hạn (thường vài chục), nên cần **đủ router cấp nguồn lưới** rải khắp khu vực để
  mỗi badge luôn có router trong tầm, và tổng sức chứa child lớn hơn số badge.
- Lưu lượng rất nhẹ: N badge → N khung/phút.
- Người đeo che chắn anten đáng kể (suy hao thêm vài dB đến hơn 10 dB). Đặt router cao, thoáng;
  thử nghiệm vùng phủ với badge đeo trên người chứ không phải cầm trên tay.
- Parent "quên" một end device im lặng quá *End Device Timeout* (firmware đặt 64 phút). Ở chế độ
  deep sleep badge rejoin mỗi phút nên không bị ảnh hưởng; ở light sleep badge poll mỗi 5 phút.
