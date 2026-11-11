# Dòng tiêu thụ và thời lượng pin

> Tất cả con số dưới đây là **ước tính** từ giá trị "typical" trong datasheet cộng với các giả định
> về thời gian thức (đánh dấu *giả định*). Chưa có số đo trên board thật. Sửa tham số trong
> [`power_budget.py`](power_budget.py) rồi chạy lại khi có số đo.

```bash
python3 docs/power_budget.py
```

## 1. Số liệu đầu vào

| Thành phần | Dòng | Nguồn |
|---|---|---|
| ESP32-H2 deep sleep (LP timer + LP memory) | 7 µA | Datasheet ESP32-H2-WROOM-02C v1.2, bảng 15 |
| ESP32-H2 light sleep, ngoại vi tắt nguồn | 25 µA | như trên (85 µA nếu ngoại vi không tắt được) |
| ESP32-H2 CPU chạy 96 MHz, radio tắt | 10–17 mA | bảng 14 |
| ESP32-H2 thu 802.15.4 | 26 mA | bảng 13 |
| ESP32-H2 phát 802.15.4 | 36 mA @ 0 dBm, 60 mA @ 9 dBm, 140 mA @ 20 dBm | bảng 13 |
| LIS2DH12, 1 Hz low-power | 2 µA | datasheet LIS2DH12 |
| LDO RT9080 (Iq) | 2 µA | datasheet RT9080 |
| Rò rỉ khác (IC sạc, diode ESD, tụ) | 1 µA | *giả định* |
| Tự xả của cell LiPo + mạch bảo vệ | ~15 µA | *giả định* (~2 %/tháng với cell 500 mAh) |

Mạch đo pin dùng công tắc P-FET nên không tiêu thụ khi không đo. LED tắt khi hoạt động bình thường.

## 2. Ngân sách điện tích mỗi phút

### Deep sleep, rejoin mỗi lần báo cáo (`CONFIG_BADGE_POWER_MODE_DEEP_SLEEP`, mặc định)

| Khoản | mC/phút | Ghi chú |
|---|---|---|
| Nền ngủ 12 µA × 60 s | 0,72 | 7 + 2 + 2 + 1 µA |
| 1 lần thức chỉ đọc FIFO | 0,80 | *giả định* 80 ms @ 10 mA (ROM + bootloader + khởi động app + I2C, tắt log) |
| Lần thức báo cáo: boot + khởi tạo stack Zigbee | 2,50 | *giả định* 220 ms |
| Lần thức báo cáo: rejoin | 9,00 | *giả định* 300 ms mở máy thu (khoảng hợp lý 0,1–1 s) |
| Lần thức báo cáo: phát report + chờ xác nhận | 1,74 | |
| **Tổng** | **14,8** | **≈ 246 µA trung bình** |

### Light sleep, giữ stack Zigbee (`CONFIG_BADGE_POWER_MODE_LIGHT_SLEEP`)

| Khoản | mC/phút | Ghi chú |
|---|---|---|
| Nền ngủ 30 µA × 60 s | 1,80 | 25 + 2 + 2 + 1 µA |
| 2 lần đọc FIFO | 0,16 | thức từ light sleep, không boot lại |
| Báo cáo: CPU + phát + MAC ack + poll lấy APS ack | 0,88 | |
| Báo pin mỗi 10 phút (2 khung), tính trung bình | 0,14 | |
| Poll giữ kết nối mỗi 5 phút, tính trung bình | 0,06 | |
| **Tổng** | **3,04** | **≈ 51 µA trung bình** |

## 3. Thời lượng pin (cell 500 mAh, dùng được ~85 % đến ngưỡng LDO sụt áp, có tính tự xả)

| Chế độ | Dòng trung bình | Thời lượng ước tính |
|---|---|---|
| Deep sleep + rejoin mỗi phút | ~246 µA | **~68 ngày** |
| Light sleep, giữ stack | ~51 µA | **~270 ngày** |

Độ nhạy của ước tính:

- Deep sleep: nếu rejoin mất 1 s thay vì 0,3 s → ~600 µA (~29 ngày); nếu chỉ 0,1 s → ~150 µA (~110 ngày).
- Light sleep: nếu miền ngoại vi không tắt được (85 µA thay vì 25 µA) → ~110 µA (~140 ngày).

Trong cả hai trường hợp xấu/tốt, kết luận không đổi: **light sleep tiết kiệm hơn deep sleep** cho
thiết kế này (mục 4).

## 4. Vì sao deep sleep không phải lựa chọn tối ưu cho thiết kế này

Deep sleep tiết kiệm được 25 − 7 = 18 µA so với light sleep, tức **1,1 mC mỗi phút**. Đổi lại nó
phải trả hai khoản:

1. **Phục vụ FIFO.** FIFO của LIS2DH12 chỉ chứa 32 mẫu nên ở 1 Hz phải được đọc ít nhất 2 lần mỗi
   phút. Ở deep sleep mỗi lần đọc là một lần boot đầy đủ (~0,8 mC); ở light sleep chỉ là một lần
   thức ngắn (~0,08 mC). Riêng khoản này (~1,6 mC/phút) đã lớn hơn phần tiết kiệm 1,1 mC/phút.
2. **Rejoin.** Mỗi lần báo cáo phải boot + khởi tạo stack + rejoin mạng (~13 mC). Theo Base Device
   Behavior của Zigbee, end device khởi động lại phải rejoin (quét kênh, Rejoin Request/Response)
   trước khi gửi dữ liệu, và máy thu tiêu thụ 26 mA trong suốt thời gian đó.

Kết quả theo chu kỳ báo cáo (cùng bộ giả định, cell 500 mAh):

| Chu kỳ báo cáo | Deep sleep | Light sleep |
|---|---|---|
| 1 phút | 246 µA (68 ngày) | 51 µA (270 ngày) |
| 2 phút | 142 µA (113 ngày) | 42 µA (310 ngày) |
| 5 phút | 80 µA (186 ngày) | 37 µA (340 ngày) |
| 15 phút | 52 µA (262 ngày) | 35 µA (356 ngày) |
| 60 phút | 42 µA (310 ngày) | 34 µA (362 ngày) |

Tức là với cách lấy mẫu 1 Hz qua FIFO, deep sleep chỉ tiệm cận chứ không vượt được light sleep.
Nó chỉ thắng nếu lần thức đọc FIFO rẻ hơn ~0,6 mC (ví dụ đọc FIFO trong wake stub, mục 6) **và**
báo cáo thưa.

Firmware vẫn để **deep sleep làm mặc định vì đó là yêu cầu của đề bài**, và vì nó có hai ưu điểm
thật: mỗi chu kỳ là một lần khởi động sạch (không tích luỹ lỗi), và mỗi báo cáo tự chọn lại router
gần nhất, phù hợp với người đeo di chuyển liên tục. Chuyển chế độ chỉ là một tuỳ chọn Kconfig; nên
đo cả hai trên board rồi quyết định.

## 5. Các tối ưu đã có trong thiết kế

1. Cảm biến lấy mẫu vào FIFO, MCU chỉ thức 2 lần/phút thay vì 60 lần.
2. Lần thức giữa phút không khởi tạo NVS, không bật radio.
3. Bootloader bỏ kiểm tra image khi thức từ deep sleep và không in log.
4. Badge chưa ghép nối không tự quét mạng (chỉ khi cấp nguồn hoặc nhấn nút).
5. Mất mạng thì giãn dần khoảng thử lại (1, 2, 4… 16 chu kỳ) thay vì quét mỗi phút.
6. Mạch chia áp đo pin chỉ được cấp điện vài ms mỗi lần đo; pin chỉ được báo mỗi 10 chu kỳ.
7. Không có điện trở kéo nào dẫn dòng tĩnh: INT1 tích cực mức cao (nghỉ ở mức thấp), I2C nghỉ ở
   mức cao, SA0/CS của LIS2DH12 nối VDD_IO.
8. LDO Iq 2 µA; không dùng thạch anh 32 kHz; LED sạc lấy điện từ VBUS chứ không từ pin.

## 6. Tối ưu thêm nếu cần (chưa làm)

| Việc | Lợi ích dự kiến |
|---|---|
| Dùng bản release (`sdkconfig.release.defaults`, tắt log) | Giảm vài chục ms thức mỗi lần boot ở deep sleep |
| Giới hạn `BADGE_ZB_CHANNEL_MASK` về đúng kênh của mạng | Rejoin/ghép nối nhanh hơn |
| Giảm `BADGE_ZB_TX_POWER_DBM` nếu vùng phủ cho phép | 60 mA → 36 mA khi phát |
| Tăng `BADGE_REPORT_SAMPLES` (2–5 phút/báo cáo) | Deep sleep: 246 → 80 µA ở 5 phút; light sleep: 51 → 37 µA (bảng mục 4) |
| Giãn báo cáo khi badge nằm yên (điểm ≈ 0 nhiều phút liền, ví dụ ngoài ca làm) và đánh thức bằng ngắt chuyển động trên INT2 | Giảm mạnh tiêu thụ ngoài giờ làm; INT2 đã được nối sẵn tới GPIO11 |
| Đọc FIFO trong deep-sleep wake stub (không boot app) | Bỏ được ~0,8 mC/phút ở deep sleep |

## 7. Cách đo

- Tháo pin, cấp nguồn 3,8–4,0 V vào hai pad pin qua Nordic PPK2 / Joulescope / Otii (dải
  động từ µA đến hàng chục mA, lấy mẫu ≥ 10 kHz để thấy xung phát).
- Tháo cáp USB và bộ USB-UART khi đo (chúng cấp dòng ngược qua chân IO).
- Đo riêng: nền ngủ (kỳ vọng ~12 µA / ~30 µA), một lần thức đọc FIFO, một lần thức báo cáo; lấy
  tích phân điện tích từng pha rồi điền vào `power_budget.py`.
- Đo trung bình dài ít nhất 10 phút để gồm cả lần báo pin.
