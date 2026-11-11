# Firmware — Activity Badge (ESP32-H2 + LIS2DH12, Zigbee Sleepy End Device)

Đã build thành công (không warning trong mã nguồn của project) với:

| Thành phần | Phiên bản |
|---|---|
| ESP-IDF | v5.5.4 (`idf.py set-target esp32h2`) |
| esp-zigbee-lib | 2.1.0 (stack Zigbee mới của Espressif, API `ezbee/*`) |
| Target | ESP32-H2-WROOM-02C-H4S (4 MB flash) |

> **Lưu ý về esp-zigbee-sdk 2.x.** Từ bản 2.0 (04/2026) SDK dùng API mới `ezb_*`/`esp_zigbee_*`
> thay cho `esp_zb_*` của 1.x. Hầu hết hướng dẫn trên mạng còn viết cho 1.x và không biên dịch
> được với 2.x. Code ở đây viết trực tiếp theo header và ví dụ `sleepy_devices` của 2.1.0.

> **Chưa chạy trên phần cứng thật.** Firmware mới được kiểm chứng ở mức biên dịch (cả hai chế độ
> ngủ) và unit test thuật toán trên PC. Các điểm cần kiểm tra khi có board nằm ở cuối file.

## 1. Cấu trúc project

```
firmware/
├── CMakeLists.txt              # project ESP-IDF, chỉ build component "main"
├── partitions.csv              # nvs 64 KB (dữ liệu mạng Zigbee) + phy + app 1.5 MB
├── sdkconfig.defaults          # cấu hình mặc định (target, Zigbee ZED, sleep, console)
├── sdkconfig.light.defaults    # overlay: chế độ light sleep
├── sdkconfig.release.defaults  # overlay: bản production (tắt log)
├── dependencies.lock           # khoá phiên bản esp-zigbee-lib
├── main/
│   ├── CMakeLists.txt
│   ├── idf_component.yml       # phụ thuộc: espressif/esp-zigbee-lib ~2.1.0
│   ├── Kconfig.projbuild       # menu "Activity badge"
│   ├── board.h                 # sơ đồ chân (khớp với hardware/)
│   ├── main.c                  # luồng chính: thức dậy → đọc FIFO → tích luỹ → báo cáo → ngủ
│   ├── lis2dh12.c/.h           # driver I2C: 1 Hz low-power, FIFO stream, ngắt watermark
│   ├── activity.c/.h           # thuật toán a_mag / a_dyn / activity_score (số nguyên, mg)
│   ├── battery.c/.h            # đo điện áp pin qua ADC + đổi ra %
│   └── zb_badge.c/.h           # Zigbee: endpoint, join/rejoin, gửi Report Attributes
└── test_host/
    └── test_activity.c         # unit test thuật toán, chạy trên PC
```

## 2. Kiến trúc: ai làm gì

Ý tưởng chính để tiết kiệm pin: **cảm biến thức, MCU ngủ**.

- LIS2DH12 chạy liên tục 1 Hz ở low-power mode (8 bit, ~2 µA) và tự lưu mẫu vào FIFO 32 mức.
  Khi FIFO đủ số mẫu yêu cầu (watermark) nó kéo INT1 lên mức cao.
- ESP32-H2 ngủ, được INT1 đánh thức, đọc cả lô mẫu bằng **một** lần đọc I2C rồi ngủ tiếp.
  Như vậy mỗi phút MCU chỉ thức 2 lần thay vì 60 lần.

Dòng thời gian một phút (mặc định 60 mẫu/báo cáo):

```
t = 0 s     LIS2DH12 lấy mẫu mỗi giây vào FIFO ............................ MCU ngủ
t ≈ 30 s    INT1↑ (30 mẫu) → MCU dậy, đọc FIFO, cộng dồn sum_dyn, count=30 → ngủ
t ≈ 60 s    INT1↑ (30 mẫu) → MCU dậy, đọc FIFO, count=60
            → activity_score = sum_dyn / count
            → lên mạng Zigbee → gửi Report Attributes → chờ xác nhận
            → reset sum_dyn, count → ngủ
```

Chi tiết đáng chú ý:

- **Watermark động.** Sau mỗi lần đọc, firmware đặt lại watermark = số mẫu còn thiếu đến lần
  báo cáo (tối đa 30). Nhờ vậy báo cáo luôn rơi đúng vào mẫu thứ 60 dù lần thức trước bị trễ.
- **Mốc thời gian là đồng hồ của cảm biến.** "1 phút" = 60 mẫu ở ODR danh định 1 Hz. ODR của
  LIS2DH12 lấy từ dao động RC nội nên có sai số vài %; điểm số vẫn đúng vì được chia cho số mẫu
  thực tế. Vì thế board không cần thạch anh 32,768 kHz.
- **Timer dự phòng.** Nếu INT1 không tới (lỗi cảm biến), MCU vẫn tự dậy sau `batch + 12 % + 4 s`,
  phát hiện cảm biến mất cấu hình và cấu hình lại.
- **Dữ liệu trong RTC memory.** `sum_dyn_mg`, `sample_count` và trạng thái back-off nằm trong
  `RTC_DATA_ATTR badge_state_t s_state` nên sống qua deep sleep.
- **Số nguyên.** Tính bằng milli-g với căn bậc hai số nguyên, không dùng soft-float trong vòng lặp
  (ESP32-H2 không có FPU). Chỉ phép chia cuối cùng ra `float` để gửi đi.

### Thuật toán (đúng theo yêu cầu)

```
a_mag = sqrt(ax² + ay² + az²)                 # activity_dyn_mg()
a_dyn = |a_mag − 1 g|
sum_dyn += a_dyn ; sample_count += 1          # activity_add(), lưu RTC memory
activity_score = sum_dyn / sample_count       # activity_score_g(), chỉ khi sample_count > 0
→ gửi Zigbee → reset sum_dyn, sample_count    # window_restart()
```

Độ phân giải ở low-power mode ±2 g là 16 mg/LSB, nên khi đứng yên điểm số dao động quanh
0,00–0,03 g (lượng tử hoá + offset của cảm biến), đi bộ thường cho 0,1–0,3 g.

### Hai chiến lược ngủ

Chọn trong `menuconfig → Activity badge → Sleep strategy between reports`.

| | Deep sleep (mặc định, đúng đề bài) | Light sleep |
|---|---|---|
| Dòng ngủ của chip (datasheet) | 7 µA | 25 µA |
| Mỗi lần thức | khởi động lại từ đầu | tiếp tục chạy, RAM còn nguyên |
| Zigbee | stack khởi động + **rejoin** mỗi lần báo cáo | stack giữ nguyên, báo cáo chỉ là một cú phát ngắn |
| Dòng trung bình ước tính (báo cáo 1 phút) | ~250 µA | ~50 µA |
| Khi người đeo di chuyển | mỗi báo cáo tự chọn lại router gần nhất | chỉ đổi router khi gửi lỗi (rejoin rồi gửi lại) |

Ở chu kỳ 1 phút, **light sleep được ước tính tiết kiệm hơn khoảng 5 lần**: chênh lệch dòng ngủ
chỉ 18 µA (≈1,1 mC mỗi phút), trong khi deep sleep phải boot lại 2 lần mỗi phút để đọc FIFO
(~0,8 mC mỗi lần) và rejoin mỗi lần báo cáo (~13 mC). Theo ước tính, deep sleep không vượt được
light sleep ở bất kỳ chu kỳ báo cáo nào, chỉ tiệm cận khi báo cáo rất thưa. Xem
[docs/power.md](../docs/power.md); đây là con số tính từ datasheet, cần đo lại trên board.

## 3. Build và nạp

```bash
. $IDF_PATH/export.sh
```

```bash
cd firmware && idf.py set-target esp32h2
```

```bash
idf.py build
```

Bản light sleep và bản production dùng thư mục build riêng:

```bash
idf.py -B build_light -D SDKCONFIG=build_light/sdkconfig -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.light.defaults" build
```

```bash
idf.py -B build_release -D SDKCONFIG=build_release/sdkconfig -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.release.defaults" build
```

Unit test thuật toán trên PC:

```bash
cd firmware/test_host && cc -Wall -Wextra -Werror -I../main test_activity.c ../main/activity.c -lm -o test_activity && ./test_activity
```

### Nạp qua USB-C (USB-Serial-JTAG tích hợp của ESP32-H2)

Vì firmware ngủ gần như liên tục nên cổng USB không tồn tại khi đang chạy ứng dụng. Đưa chip vào
download mode bằng tay: **giữ BOOT (SW1), nhấn RESET (SW2), thả BOOT**, rồi:

```bash
idf.py -p /dev/ttyACM0 flash
```

Nhấn RESET để chạy. Lần đầu nên xoá sạch flash (xoá cả dữ liệu mạng Zigbee cũ):

```bash
idf.py -p /dev/ttyACM0 erase-flash
```

### Xem log

Console nằm trên **UART0** (pad TX = TXD0/GPIO24, pad RX = RXD0/GPIO23 ở mặt dưới board, 115200 baud)
vì USB không sống qua các chế độ ngủ. Nối một bộ USB-UART 3,3 V vào hai pad này và GND:

```bash
idf.py -p /dev/ttyUSB0 monitor
```

### Thử trên devkit trước khi có PCB

ESP32-H2-DevKitM-1 + module LIS2DH12: nối SDA→GPIO4, SCL→GPIO5, INT1→GPIO10, 3V3, GND
(SA0 = mức cao → địa chỉ 0x19; nếu breakout kéo SA0 xuống GND thì sửa `BOARD_LIS2DH12_ADDR` thành
0x18). Nút BOOT của devkit đã ở GPIO9. Tắt `CONFIG_BADGE_BATTERY_SENSE` vì devkit không có mạch đo pin.

## 4. Cấu hình menuconfig

`idf.py menuconfig` → **Activity badge**:

| Tuỳ chọn | Mặc định | Ý nghĩa |
|---|---|---|
| `BADGE_POWER_MODE_*` | deep sleep | Chiến lược ngủ (mục 2) |
| `BADGE_REPORT_SAMPLES` | 60 | Số mẫu (= số giây) cho một báo cáo |
| `BADGE_BACKOFF_MAX_PERIODS` | 16 | Giãn cách tối đa giữa các lần thử khi mất mạng (1, 2, 4… chu kỳ) |
| `BADGE_JOIN_WINDOW_S` | 180 | Thời gian tìm mạng để ghép nối sau khi cấp nguồn / nhấn nút |
| `BADGE_SERVICE_WINDOW_S` | 45 | Thời gian thức (poll 1 s) để Zigbee2MQTT interview |
| `BADGE_REJOIN_TIMEOUT_MS` | 8000 | Thời gian tối đa để vào lại mạng trước mỗi báo cáo |
| `BADGE_BATTERY_SENSE` | y | Đo và báo điện áp pin |
| `BADGE_BATTERY_REPORT_PERIOD` | 10 | Báo pin mỗi N báo cáo activity |
| `BADGE_ZB_CHANNEL_MASK` | 0x07FFF800 | Kênh quét khi ghép nối; đặt đúng 1 kênh của mạng để ghép nhanh hơn |
| `BADGE_ZB_TX_POWER_DBM` | 10 | Công suất phát (0 dBm ≈ 36 mA, 9 dBm ≈ 60 mA, 20 dBm ≈ 140 mA) |
| `BADGE_ZB_POLL_INTERVAL_S` | 300 | Chu kỳ poll parent khi rảnh (chỉ có ý nghĩa ở light sleep) |
| `BADGE_ZB_MANUFACTURER` / `BADGE_ZB_MODEL` | ActivityBadge / AB-H2-LIS2DH12 | Chuỗi Basic cluster; converter Z2M nhận diện theo model |

Các mục ngoài menu này đã được đặt sẵn trong `sdkconfig.defaults`, không cần sửa tay:

| Mục trong menuconfig | Giá trị | Lý do |
|---|---|---|
| Component config → Zigbee → Zigbee Enable | y | bật stack |
| Component config → Zigbee → Device type | Zigbee End Device | thư viện `zed` |
| Partition Table | Custom `partitions.csv` | NVS 64 KB cho dữ liệu mạng |
| Serial flasher config → Flash size | 4 MB | module -H4S |
| ESP System Settings → Console | UART0, secondary = none | USB-Serial-JTAG không dùng được khi ngủ |
| Bootloader → Skip image validation when exiting deep sleep | y | boot nhanh hơn sau deep sleep |
| Bootloader → Log verbosity | No output | giảm thời gian thức |
| Hardware Settings → RTC clock source | Internal RC | không dùng thạch anh 32 kHz |
| Power Management → Enable + FreeRTOS tickless idle + Power down peripherals in light sleep | y | light sleep tự động ~25 µA |
| IEEE 802.15.4 → Enable sleep | y | radio ngủ theo light sleep |

## 5. Hành vi khi sử dụng

| Sự kiện | Hành vi |
|---|---|
| Cấp nguồn / RESET | Cấu hình cảm biến, khởi động Zigbee. Nếu chưa ghép nối: tìm mạng trong 180 s (LED sáng). Nếu đã ghép nối: rejoin và báo pin. |
| Ghép nối thành công | LED sáng 45 s, poll mỗi giây để Z2M interview, sau đó vào chu kỳ ngủ. |
| Nhấn nút ngắn | Thức dậy, (ghép nối nếu chưa), giữ liên lạc 45 s, gửi báo cáo ngay. Dùng khi cần Z2M interview/cấu hình lại. |
| Giữ nút ≥ 5 s | LED nháy 3 lần, xoá dữ liệu mạng Zigbee và khởi động lại (factory new). |
| Mất mạng | Tiếp tục cộng dồn mẫu; thử lại sau 1, 2, 4… tối đa 16 chu kỳ báo cáo. Khi có mạng lại, gửi một điểm số là trung bình của cả khoảng mất mạng. |
| Badge chưa ghép nối | Không bật radio (chỉ tìm mạng khi cấp nguồn hoặc nhấn nút) để không hao pin khi nằm kho. |

## 6. Cần kiểm tra trên phần cứng thật

1. **Dòng tiêu thụ** từng pha (ngủ, thức đọc FIFO, thức báo cáo) bằng PPK2/Joulescope, so với
   `docs/power_budget.py`; quyết định deep sleep hay light sleep theo số đo.
2. **Thời gian boot + rejoin** ở deep sleep (giả định 300 ms mở máy thu). Đây là yếu tố quyết định
   dòng trung bình của chế độ này.
3. **Lệnh `ezb_zcl_report_attr_cmd_req`** gửi thẳng tới coordinator (0x0000, endpoint 1) không cần
   binding: xác nhận Z2M nhận được `activity_score`, `battery`, `voltage`.
4. **Rejoin chủ động** (`zb_badge_reconnect()` gọi BDB initialization khi stack vẫn nghĩ là đang
   ở trong mạng): kiểm tra bằng cách tắt router đang làm parent.
5. **Light sleep có xuống ~25 µA không**: bật `CONFIG_ESP_SLEEP_DEBUG` và kiểm tra cờ `PMU_SLEEP_PD_TOP`.
6. **Đánh thức bằng INT1 ở light sleep** (EXT1 + ngắt mức GPIO). Nếu ngắt không tới, timeout dự
   phòng vẫn đọc FIFO nên dữ liệu không mất, nhưng nên xác nhận bằng log.
7. **Hướng dữ liệu FIFO ở low-power mode**: firmware lấy byte cao của mỗi trục (8 bit canh trái).
   Đặt badge nằm yên và kiểm tra |a| ≈ 1000 mg trên trục thẳng đứng.
