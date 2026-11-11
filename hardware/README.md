# Phần cứng — Activity Badge (ESP32-H2-WROOM-02C + LIS2DH12)

## 1. File trong thư mục này

| File | Nội dung |
|---|---|
| `kicad/activity-badge.kicad_pro`, `.kicad_sch` | Project KiCad (định dạng KiCad 7, mở được bằng KiCad 7/8/9) |
| `kicad/badge.kicad_sym` | Symbol ESP32-H2-WROOM-02C (thư viện chuẩn của KiCad và của Espressif chưa có module này) |
| `kicad/badge.pretty/ESP32-H2-WROOM-02C.kicad_mod` | Footprint module, vẽ theo "Recommended PCB Land Pattern" trong datasheet v1.2, có sẵn vùng cấm đồng dưới anten |
| `out/activity-badge.pdf` | Sơ đồ nguyên lý dạng PDF |
| `out/activity-badge.net` | Netlist KiCad (Pcbnew: *File → Import → Netlist*) |
| `out/bom.csv` | BOM |
| `gen/gen_kicad.py` | Script sinh toàn bộ các file trên từ một danh sách linh kiện + net duy nhất, rồi tự kiểm tra: so khớp netlist xuất từ KiCad với netlist thiết kế, kiểm tra footprint tồn tại và đủ pad |

Sinh lại và kiểm tra (cần KiCad ≥ 7 để có `kicad-cli`):

```bash
python3 hardware/gen/gen_kicad.py
```

Bắt đầu layout: mở project, vào PCB Editor, *Tools → Update PCB from Schematic*.

> Schematic được sinh bằng script theo kiểu "nhãn net" (mỗi IC là một khối, chân nối bằng nhãn),
> đúng về điện nhưng không đẹp như vẽ tay. KiCad 7 không có lệnh ERC dòng lệnh, nên việc kiểm tra
> ở đây là so khớp netlist; hãy chạy ERC trong giao diện KiCad trước khi layout.

## 2. Cây nguồn

```
USB-C VBUS 5 V ──► MCP73831 (sạc 213 mA) ──► VBAT (LiPo 1S 3,0–4,2 V) ──► RT9080-3.3 (Iq 2 µA) ──► +3V3
                                                │                                      ├─ ESP32-H2-WROOM-02C
                                                └─ Q1/Q2 + chia áp 1:2 ─► ADC (GPIO1)  └─ LIS2DH12 (luôn cấp điện)
```

- **LDO thay vì buck**: hiệu suất LDO từ 3,8 V xuống 3,3 V đã ~87 %, và ở tải µA dòng tĩnh của bộ
  nguồn mới là yếu tố quyết định. RT9080 có Iq 2 µA và chịu được 600 mA (datasheet module yêu cầu
  nguồn cấp ≥ 350 mA).
- Khi pin xuống dưới ~3,4 V LDO vào vùng sụt áp, +3V3 bám theo VBAT; module làm việc tới 3,0 V.
- LIS2DH12 không được cắt nguồn: nó phải lấy mẫu liên tục trong lúc MCU ngủ.
- Cell LiPo phải là loại **có mạch bảo vệ (PCM)**. Luôn cắm pin khi dùng: MCP73831 không ổn định
  nếu chạy không pin.

## 3. Sơ đồ chân ESP32-H2-WROOM-02C

| Chân module | Tên | GPIO | Net | Nối tới | Ghi chú |
|---|---|---|---|---|---|
| 1 | 3V3 | — | +3V3 | LDO U3, C5 10 µF + C6 100 nF | tụ đặt sát chân |
| 2 | EN | — | EN | SW2 (RESET), TP6 | module đã có sẵn RC 10 kΩ/1 µF |
| 3 | IO4 | GPIO4 | I2C_SDA | LIS2DH12 chân 4, R5 4,7 kΩ lên +3V3 | |
| 4 | IO5 | GPIO5 | I2C_SCL | LIS2DH12 chân 1, R6 4,7 kΩ lên +3V3 | |
| 5 | IO10 | GPIO10 | ACC_INT1 | LIS2DH12 chân 12 | **LP GPIO**: đánh thức từ deep sleep |
| 6 | IO11 | GPIO11 | ACC_INT2 | LIS2DH12 chân 11 | LP GPIO, dự phòng (firmware chưa dùng) |
| 7 | IO8 | GPIO8 | IO8_STRAP | R8 10 kΩ lên +3V3 | strapping: phải ở mức cao để vào download mode |
| 8 | IO9 | GPIO9 | BOOT | SW1, R7 10 kΩ lên +3V3, C11 100 nF, TP7 | strapping BOOT + nút người dùng, LP GPIO |
| 9, 13, 29 | GND | — | GND | | 29 = pad tản nhiệt dưới module |
| 10 | IO12 | GPIO12 | — | không nối | LP GPIO còn trống |
| 11, 12 | IO13, IO14 | GPIO13/14 | — | không nối | chân XTAL 32 kHz, không dùng |
| 14 | VBAT | — | +3V3 | | mặc định nối 3V3 bên trong module |
| 15 | IO22 | GPIO22 | LED_STATUS | R9 2,2 kΩ → D1 → GND | tích cực mức cao |
| 16–19 | NC | — | — | không nối | |
| 20 | IO25 | GPIO25 | — | không nối | strapping nguồn JTAG, bị bỏ qua với eFuse mặc định |
| 21 | RXD0 | GPIO23 | UART_RX | TP5 | console/nạp qua UART |
| 22 | TXD0 | GPIO24 | UART_TX | TP4 | console (115200) |
| 23 | IO26 | GPIO26 | USB_DN | R15 0 Ω → U5 → USB-C D− | USB-Serial-JTAG |
| 24 | IO27 | GPIO27 | USB_DP | R14 0 Ω → U5 → USB-C D+ | USB-Serial-JTAG |
| 25, 26 | IO3, IO2 | GPIO3/2 | — | không nối | còn trống (ADC1_CH2/CH1) |
| 27 | IO1 | GPIO1 | VBAT_SENSE | điểm giữa R12/R13, C10 10 nF | ADC1_CH0 |
| 28 | IO0 | GPIO0 | VBAT_SENSE_EN | cực G của Q2, R11 1 MΩ xuống GND | mức cao = bật mạch chia áp |

### Kết nối ESP32-H2-WROOM-02C ↔ LIS2DH12 (LGA-12)

| LIS2DH12 | Tên | Nối tới | Ghi chú |
|---|---|---|---|
| 1 | SCL/SPC | GPIO5 (module chân 4) | I2C 400 kHz, kéo lên 4,7 kΩ |
| 2 | CS | +3V3 | mức cao = chế độ I2C |
| 3 | SDO/SA0 | +3V3 | địa chỉ I2C 0x19 (nối GND thì là 0x18) |
| 4 | SDA/SDI/SDO | GPIO4 (module chân 3) | kéo lên 4,7 kΩ |
| 5 | Res | GND | |
| 6, 7, 8 | GND | GND | |
| 9 | VDD | +3V3 | C7 100 nF + C8 10 µF sát chân |
| 10 | VDD_IO | +3V3 | C9 100 nF sát chân |
| 11 | INT2 | GPIO11 (module chân 6) | dự phòng: ngắt chuyển động/rơi tự do |
| 12 | INT1 | GPIO10 (module chân 5) | ngắt watermark FIFO, tích cực mức cao, push-pull |

Vì sao chọn các chân này:

- **INT1/INT2 phải vào LP GPIO (GPIO7–14)** vì chỉ các chân này dùng được làm nguồn đánh thức EXT1
  khi deep sleep. GPIO8/9 là strapping, GPIO13/14 là chân thạch anh 32 kHz, nên còn GPIO10/11/12.
- I2C trên GPIO4/5: không phải strapping, nằm cạnh GPIO10/11 trên cùng một cạnh module nên bốn
  đường tới cảm biến đi song song, ngắn. (GPIO2–5 là chân JTAG ngoài, nhưng mặc định ESP32-H2 dùng
  JTAG qua USB nên các chân này tự do.)
- GPIO26/27 dành cho USB, GPIO23/24 cho UART0, GPIO0/1 cho đo pin (GPIO1 có ADC).

## 4. Các khối mạch

**USB-C + ESD.** J1 (USB 2.0, 16 chân). CC1, CC2 mỗi chân một điện trở 5,1 kΩ xuống GND để nguồn
USB-C cấp 5 V. D+/D− qua U5 USBLC6-2SC6 rồi R14/R15 (0 Ω, chỗ để chỉnh tín hiệu) tới GPIO27/26.
Cổng này vừa sạc vừa nạp firmware/JTAG qua USB-Serial-JTAG của ESP32-H2, không cần chip USB-UART.

**Sạc LiPo.** U4 MCP73831T-2ACI/OT, 4,20 V. R3 = 4,7 kΩ → dòng sạc 1000 V / 4,7 kΩ ≈ 213 mA (hợp
với cell ≥ 400 mAh; đổi R3 nếu dùng cell khác: 10 kΩ → 100 mA, 2 kΩ → 500 mA). D2 sáng khi đang sạc
(VBUS → R4 → D2 → STAT), lấy điện từ VBUS nên không hao pin.

**LDO 3,3 V.** U3 RT9080-33GJ5, EN nối VBAT (luôn bật). C3 1 µF ở ngõ vào, C4 22 µF ở ngõ ra để đỡ
bước tải từ µA lên hàng chục mA khi radio bật.

**MCU.** U1 ESP32-H2-WROOM-02C-H4S (4 MB flash, anten PCB). R8 kéo GPIO8 lên, R7 kéo GPIO9 lên.

**Gia tốc kế.** U2 LIS2DH12TR, I2C địa chỉ 0x19.

**Đo pin có công tắc.** Q1 (P-MOSFET AO3401A) nối VBAT vào mạch chia R12/R13 (100 kΩ/100 kΩ, 1 %)
chỉ khi GPIO0 ở mức cao (qua Q2 2N7002 kéo cực G của Q1 xuống). Khi tắt, dòng tiêu thụ chỉ còn dòng
rò của MOSFET và chân ADC ở 0 V (không bao giờ vượt quá 3,3 V). C10 10 nF giữ điện áp cho ADC lấy
mẫu; hằng số thời gian 0,5 ms nên firmware chờ 5 ms rồi đo.
Phương án đơn giản hơn nếu chấp nhận ~2 µA liên tục: bỏ Q1/Q2/R10/R11, dùng chia áp 1 MΩ/1 MΩ + 100 nF.

**Nút và LED.** SW1 = BOOT/người dùng (GPIO9): nhấn ngắn để thức và ghép nối, giữ 5 s để factory
reset, giữ khi nhấn RESET để vào download mode. SW2 = RESET (EN). D1 = LED trạng thái.

**Test point.** TP1 +3V3, TP2 GND, TP3 VBAT, TP4 UART_TX, TP5 UART_RX, TP6 EN, TP7 BOOT: đủ để nạp và
kiểm tra bằng đồ gá pogo-pin trong sản xuất.

## 5. BOM

Bản đầy đủ theo từng mã tham chiếu: [`out/bom.csv`](out/bom.csv). Mã linh kiện của điện trở, tụ,
LED và nút nhấn trong file đó chỉ là gợi ý ("or equivalent"), chưa đối chiếu với nhà phân phối.

| Ref | Giá trị / mã linh kiện | Footprint KiCad | Ghi chú |
|---|---|---|---|
| U1 | ESP32-H2-WROOM-02C-H4S | `badge:ESP32-H2-WROOM-02C` | 18 × 20 × 3,2 mm |
| U2 | LIS2DH12TR | `Package_LGA:LGA-12_2x2mm_P0.5mm` | |
| U3 | RT9080-33GJ5 | `Package_TO_SOT_SMD:TSOT-23-5` | thay thế cùng chân: XC6220B331MR-G (Iq ~8 µA), TPS7A0533PDBVR (Iq 1 µA, 200 mA) |
| U4 | MCP73831T-2ACI/OT | `Package_TO_SOT_SMD:SOT-23-5` | |
| U5 | USBLC6-2SC6 | `Package_TO_SOT_SMD:SOT-23-6` | |
| Q1 | AO3401A | `Package_TO_SOT_SMD:SOT-23` | P-MOSFET |
| Q2 | 2N7002 | `Package_TO_SOT_SMD:SOT-23` | N-MOSFET (hoặc BSS138) |
| J1 | USB-C 16P, HRO TYPE-C-31-M-12 | `Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12` | |
| J2 | JST S2B-PH-SM4-TB | `Connector_JST:JST_PH_S2B-PH-SM4-TB_1x02-1MP_P2.00mm_Horizontal` | đầu nối pin |
| SW1, SW2 | nút nhấn TL3342 | `Button_Switch_SMD:SW_SPST_TL3342` | |
| D1 | LED xanh lá 0603 | `LED_SMD:LED_0603_1608Metric` | trạng thái |
| D2 | LED cam 0603 | `LED_SMD:LED_0603_1608Metric` | đang sạc |
| R1, R2 | 5,1 kΩ | `Resistor_SMD:R_0402_1005Metric` | CC1, CC2 |
| R3 | 4,7 kΩ 1 % | 0402 | PROG: dòng sạc 213 mA |
| R4 | 1 kΩ | 0402 | LED sạc |
| R5, R6 | 4,7 kΩ | 0402 | kéo lên I2C |
| R7, R8 | 10 kΩ | 0402 | kéo lên GPIO9, GPIO8 |
| R9 | 2,2 kΩ | 0402 | LED trạng thái |
| R10, R11 | 1 MΩ | 0402 | phân cực Q1, Q2 |
| R12, R13 | 100 kΩ 1 % | 0402 | chia áp pin |
| R14, R15 | 0 Ω | 0402 | nối tiếp USB D+/D− |
| C1 | 10 µF 16 V X5R | `Capacitor_SMD:C_0603_1608Metric` | VBUS |
| C2 | 10 µF | 0603 | VBAT |
| C3 | 1 µF | 0402 | ngõ vào LDO |
| C4 | 22 µF | `Capacitor_SMD:C_0805_2012Metric` | ngõ ra LDO |
| C5 | 10 µF | 0603 | module 3V3 |
| C6, C7, C9, C11 | 100 nF | `Capacitor_SMD:C_0402_1005Metric` | lọc nguồn module, VDD, VDD_IO; chống rung nút |
| C8 | 10 µF | 0603 | VDD cảm biến |
| C10 | 10 nF | 0402 | lọc ADC |
| TP1–TP7 | pad thử 1 mm | `TestPoint:TestPoint_Pad_D1.0mm` | |
| — | Cell LiPo 1S 400–600 mAh có PCM, đầu JST-PH | — | ví dụ cỡ 502535 |

## 6. Lưu ý layout PCB

### Số lớp

- **Khuyến nghị 4 lớp** (tín hiệu / GND / nguồn / tín hiệu), FR-4 1,6 mm: có mặt GND liền ngay
  dưới lớp linh kiện nên board nhỏ gọn hơn, chống ESD/EMI tốt hơn (thẻ đeo ngoài công trường, có
  cổng USB và nút nhấn hở), và chênh lệch giá ở số lượng nhỏ không đáng kể.
- **2 lớp vẫn làm được**: phần RF nằm trọn trong module, trên board không có đường trở kháng kiểm
  soát ngoài đoạn USB Full-Speed rất ngắn. Điều kiện là lớp dưới giữ được mặt GND liền, tín hiệu đi
  lớp trên. Hợp cho bản mẫu hoặc khi cần tối ưu giá.

### Anten (quan trọng nhất)

- Đặt module **sát mép board, phần anten (6 mm trên cùng của module) hướng ra ngoài**; tốt nhất để
  phần anten nhô ra khỏi mép board hoặc khoét bỏ FR-4 bên dưới.
- **Không đồng trên mọi lớp** dưới và quanh vùng anten: không GND, không đường tín hiệu, không via.
  Footprint đã có sẵn vùng keep-out 19 × 6,5 mm; nới thêm sang hai bên nếu còn chỗ. Theo hướng dẫn
  thiết kế của Espressif, nên giữ khoảng trống ~15 mm quanh anten khỏi kim loại.
- Pin LiPo (vỏ nhôm), đầu nối USB, vít và kẹp kim loại của thẻ **không được nằm chồng hoặc sát vùng anten**.
- Thẻ đeo trên người: cơ thể hấp thụ sóng 2,4 GHz. Bố trí để mặt anten hướng ra ngoài, xa thân
  người nhất có thể, và đo vùng phủ khi đang đeo thật.
- Vỏ hộp: nhựa (ABS/PC), không sơn dẫn điện, không tem kim loại ở vùng anten.

### Nguồn và tụ lọc

- C5 (10 µF) + C6 (100 nF) cách chân 3V3 của module không quá 2–3 mm, via GND ngay cạnh tụ.
- C4 (22 µF) sát chân ra của LDO, C3 sát chân vào. C1, C2 sát chân VDD và VBAT của MCP73831.
- Đường VBAT → LDO → module rộng ≥ 0,4 mm. Nhánh cấp cho LIS2DH12 rẽ ra sau tụ của module.
- C7/C8 sát chân 9 (VDD), C9 sát chân 10 (VDD_IO) của LIS2DH12.

### Mặt phẳng GND

- Mặt GND liền dưới toàn bộ mạch (trừ vùng anten), không bị đường tín hiệu cắt ngang.
- Via GND cạnh các chân GND 9, 13 của module. Footprint chưa có via trong pad tản nhiệt 29: thêm
  vài via GND (lỗ ~0,3 mm) vào hai mảng pad này khi layout, như hình land pattern của datasheet.
- GND của vỏ USB-C nối thẳng mặt GND bằng nhiều via.

### LIS2DH12

- Đặt ở vùng board **cứng và ít bị uốn**: xa nút nhấn, đầu nối pin/USB, lỗ vít và mép board. Ứng suất
  cơ học lên vỏ LGA làm lệch offset.
- **Không đi dây, không đặt via ở lớp trên bên dưới thân linh kiện.**
- Xa nguồn nhiệt (IC sạc, LDO): nhiệt khi sạc làm trôi offset.
- Các đường tới pad đối xứng, cùng bề rộng, để linh kiện không bị kéo lệch khi hàn reflow. Không
  hàn tay bằng mỏ hàn; dùng reflow theo profile của ST.
- In hướng trục X/Y/Z lên silkscreen. Thuật toán dùng |a| nên không phụ thuộc hướng lắp, nhưng cần
  khi gỡ lỗi.

### Tín hiệu

- I2C (SDA/SCL) ngắn, đi cạnh nhau, tránh xa anten và đường USB.
- USB D+/D− đi thành cặp, ngắn (< 20 mm), U5 đặt sát đầu nối, R1/R2 sát chân CC.
- INT1 đi thẳng, không cần điện trở kéo (ngõ ra push-pull, nghỉ ở mức thấp).
- Test point xếp thành một hàng, bước 2,54 mm, ở mặt dễ áp đồ gá.

### Cơ khí và môi trường công trường

- Cố định pin bằng băng keo xốp, có chỗ giữ dây; đầu JST nằm ở mép board.
- Bụi, mồ hôi, mưa: phủ conformal coating (che vùng USB-C, nút, test point) hoặc dùng vỏ có gioăng.
  Có thể thay USB-C bằng tiếp điểm sạc từ tính/pogo nếu cần kín nước.
- Thêm TVS/điện trở nối tiếp cho nút nhấn nếu vỏ có khe hở gần nút (ESD từ tay người).

## 7. Kiểm tra khi lắp board đầu tiên

1. Chưa hàn module: cấp VBAT 3,8 V, đo +3V3 = 3,30 V, dòng không tải cỡ vài µA.
2. Cắm USB không pin thật mà dùng nguồn giả lập pin: D2 sáng, dòng sạc ≈ 213 mA.
3. Hàn module, nạp firmware, xem log trên TP4: thấy `LIS2DH12 running`.
4. Đo INT1 bằng oscilloscope: lên mức cao mỗi ~30 s, xuống ngay khi MCU đọc FIFO.
5. Đo dòng ngủ tại VBAT (xem [docs/power.md](../docs/power.md)): kỳ vọng ~12 µA ở deep sleep.
6. Ghép nối với Zigbee2MQTT (xem [docs/zigbee2mqtt.md](../docs/zigbee2mqtt.md)).

## 8. Cần xác minh trước khi đặt hàng

Thiết kế này chưa được chế tạo. Trước khi đặt PCB/linh kiện, hãy đối chiếu:

1. **Thứ tự chân RT9080-33GJ5** (1 VIN, 2 GND, 3 EN, 4 NC, 5 VOUT) với datasheet Richtek. Đây là
   thứ tự chân SOT-23-5 phổ biến của LDO và trùng với các linh kiện thay thế nêu trong BOM, nhưng
   tôi không truy cập được datasheet Richtek để kiểm tra trực tiếp.
2. **Thứ tự chân LIS2DH12** với datasheet ST. Bảng ở mục 3 khớp với symbol LIS2DE12 (cùng họ, cùng
   vỏ LGA-12) trong thư viện KiCad, nhưng tôi không tải được datasheet từ st.com.
3. **Footprint ESP32-H2-WROOM-02C**: in tỉ lệ 1:1 và ướm với module thật. Kích thước lấy từ hình
   "Recommended PCB Land Pattern" của datasheet v1.2 (pad 0,9 × 1,5 mm, bước 1,5 mm, 9 + 10 + 9 pad,
   pad GND gồm hai mảng 2,8 × 0,55 mm và 2,8 × 1,6 mm).
4. **Cực tính đầu nối pin JST-PH**: không có chuẩn chung giữa các nhà bán cell. J2 chân 1 = VBAT (+),
   chân 2 = GND. Mạch không có bảo vệ ngược cực.
5. **Footprint USB-C và nút nhấn** theo đúng mã linh kiện mua được.
6. Chạy **ERC và DRC** trong KiCad sau khi layout.
7. Nên bổ sung trong firmware ngưỡng **khoá khi pin yếu** (ví dụ dưới 3,3 V thì ngừng phát sóng) trước
   khi sản xuất, để tránh vòng lặp reset khi pin cạn; hiện phần này dựa vào mạch bảo vệ của cell.
