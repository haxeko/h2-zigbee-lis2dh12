#!/usr/bin/env python3
"""
gen_kicad.py - single source of truth for the Activity Badge hardware.

The design lives in this file as data:
    PARTS  - reference, value, library symbol, footprint, MPN, description
    NETS   - net name -> list of "REF.PIN"
    NC     - pins that are intentionally left unconnected
    PLACE  - where every symbol sits on the A3 sheet

Running it (python3 hardware/gen/gen_kicad.py) writes:
    hardware/kicad/activity-badge.kicad_pro / .kicad_sch   KiCad 7 project (opens in 7, 8, 9)
    hardware/kicad/badge.kicad_sym                         project symbols (ESP32-H2-WROOM-02C, VBAT)
    hardware/kicad/badge.pretty/ESP32-H2-WROOM-02C.kicad_mod
    hardware/kicad/sym-lib-table, fp-lib-table
    hardware/out/bom.csv
and, when kicad-cli is installed:
    hardware/out/activity-badge.net   netlist exported from the schematic
    hardware/out/activity-badge.pdf   schematic plot
then it VERIFIES the exported netlist against NETS/NC and checks every footprint.

No third-party Python packages are needed. The stock KiCad symbol and footprint
libraries must be installed (override the paths with KICAD_SYMBOL_DIR and
KICAD_FOOTPRINT_DIR).
"""
import copy
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HW = os.path.dirname(HERE)
KICAD_DIR = os.path.join(HW, "kicad")
OUT_DIR = os.path.join(HW, "out")
SYM_DIR = os.environ.get("KICAD_SYMBOL_DIR", "/usr/share/kicad/symbols")
FP_DIR = os.environ.get("KICAD_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
PROJECT = "activity-badge"
TITLE = "Activity Badge \u2014 ESP32-H2 + LIS2DH12"
REV = "A"
DATE = "2026-11-11"
UUID_NS = uuid.UUID("6f0c1d5e-3b1a-4c55-9d53-0a2c1f2e7b10")

# =============================================================================
# 1. DESIGN DATA
# =============================================================================

R0402 = "Resistor_SMD:R_0402_1005Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
SOT23 = "Package_TO_SOT_SMD:SOT-23"
TPAD = "TestPoint:TestPoint_Pad_D1.0mm"
EQ = "or equivalent"

# (ref, value, symbol, footprint, MPN, description, notes)
PARTS = [
    ("U1", "ESP32-H2-WROOM-02C-H4S", "badge:ESP32-H2-WROOM-02C", "badge:ESP32-H2-WROOM-02C",
     "ESP32-H2-WROOM-02C-H4S", "Espressif 802.15.4 / BLE module, ESP32-H2, 4 MB flash, PCB antenna",
     "Project-local symbol + footprint (datasheet v1.2 land pattern)"),
    ("U2", "LIS2DH12TR", "Sensor_Motion:LIS2DE12", "Package_LGA:LGA-12_2x2mm_P0.5mm",
     "LIS2DH12TR", "ST 3-axis accelerometer, I2C, LGA-12 2x2 mm",
     "Drawn with the pin-compatible LIS2DE12 library symbol; I2C address 0x19 (SA0 = 1)"),
    ("U3", "RT9080-33GJ5", "Regulator_Linear:TPS7A0533PDBV", "Package_TO_SOT_SMD:TSOT-23-5",
     "RT9080-33GJ5", "Richtek 3.3 V LDO, 600 mA, Iq 2 uA, TSOT-23-5",
     "Pinout 1=VIN 2=GND 3=EN 4=NC 5=VOUT - verify against the datasheet; same-pinout alternates: "
     "TPS7A0533PDBV (200 mA), XC6220B331MR-G"),
    ("U4", "MCP73831T-2ACI/OT", "Battery_Management:MCP73831-2-OT", "Package_TO_SOT_SMD:SOT-23-5",
     "MCP73831T-2ACI/OT", "Microchip single-cell Li-Po charger, 4.20 V, SOT-23-5", ""),
    ("U5", "USBLC6-2SC6", "Power_Protection:USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6",
     "USBLC6-2SC6", "ST USB ESD protection, SOT-23-6", ""),
    ("Q1", "AO3401A", "Transistor_FET:AO3401A", SOT23,
     "AO3401A", "P-channel MOSFET -30 V, SOT-23 (battery-sense high-side switch)", EQ),
    ("Q2", "2N7002", "Transistor_FET:2N7002", SOT23,
     "2N7002", "N-channel MOSFET 60 V, SOT-23 (level shifter for Q1 gate)", EQ),
    ("J1", "USB-C 16P", "Connector:USB_C_Receptacle_USB2.0_16P",
     "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
     "TYPE-C-31-M-12", "USB Type-C receptacle, USB 2.0, 16 pin, SMD (HRO)", ""),
    ("J2", "LiPo pads", "Connector_Generic:Conn_01x02",
     "badge:BatteryPads_1x02_P4.0mm",
     "", "Solder pads for the LiPo leads (bottom side, 2.0 x 3.0 mm, 4 mm pitch)",
     "Pad 1 = BAT+, pad 2 = BAT-. No part to fit: solder the cell leads and secure them"),
    ("SW1", "BOOT/USER", "Switch:SW_Push", "Button_Switch_SMD:SW_SPST_B3U-1000P",
     "B3U-1000P", "Tactile switch SPST 3 x 2.5 mm, top actuated (Omron B3U)",
     "GPIO9: boot strap + user button; " + EQ),
    ("SW2", "RESET", "Switch:SW_Push", "Button_Switch_SMD:SW_SPST_B3U-1000P",
     "B3U-1000P", "Tactile switch SPST 3 x 2.5 mm, top actuated (Omron B3U)", "EN to GND; " + EQ),
    ("D1", "GREEN", "Device:LED", "LED_SMD:LED_0603_1608Metric",
     "LTST-C191KGKT", "LED green 0603 (status)", EQ),
    ("D2", "ORANGE", "Device:LED", "LED_SMD:LED_0603_1608Metric",
     "LTST-C191KFKT", "LED orange 0603 (charging)", EQ),
    ("R1", "5.1k", "Device:R", R0402, "RC0402FR-075K1L", "Resistor 5.1 k 1% 0402 (USB-C CC1 Rd)", EQ),
    ("R2", "5.1k", "Device:R", R0402, "RC0402FR-075K1L", "Resistor 5.1 k 1% 0402 (USB-C CC2 Rd)", EQ),
    ("R3", "4.7k", "Device:R", R0402, "RC0402FR-074K7L", "Resistor 4.7 k 1% 0402 (charge current 213 mA)",
     "Ichg = 1000 V / R3; " + EQ),
    ("R4", "1k", "Device:R", R0402, "RC0402FR-071KL", "Resistor 1 k 1% 0402 (charge LED)", EQ),
    ("R5", "4.7k", "Device:R", R0402, "RC0402FR-074K7L", "Resistor 4.7 k 1% 0402 (I2C SDA pull-up)", EQ),
    ("R6", "4.7k", "Device:R", R0402, "RC0402FR-074K7L", "Resistor 4.7 k 1% 0402 (I2C SCL pull-up)", EQ),
    ("R7", "10k", "Device:R", R0402, "RC0402FR-0710KL", "Resistor 10 k 1% 0402 (GPIO9/BOOT pull-up)", EQ),
    ("R8", "10k", "Device:R", R0402, "RC0402FR-0710KL", "Resistor 10 k 1% 0402 (GPIO8 strap pull-up)", EQ),
    ("R9", "2.2k", "Device:R", R0402, "RC0402FR-072K2L", "Resistor 2.2 k 1% 0402 (status LED)", EQ),
    ("R10", "1M", "Device:R", R0402, "RC0402FR-071ML", "Resistor 1 M 1% 0402 (Q1 gate pull-up)", EQ),
    ("R11", "1M", "Device:R", R0402, "RC0402FR-071ML", "Resistor 1 M 1% 0402 (Q2 gate pull-down)", EQ),
    ("R12", "100k 1%", "Device:R", R0402, "RC0402FR-07100KL", "Resistor 100 k 1% 0402 (VBAT divider, top)", EQ),
    ("R13", "100k 1%", "Device:R", R0402, "RC0402FR-07100KL", "Resistor 100 k 1% 0402 (VBAT divider, bottom)", EQ),
    ("R14", "0R", "Device:R", R0402, "RC0402JR-070RL", "Resistor 0 R 0402 (USB D+ series)", EQ),
    ("R15", "0R", "Device:R", R0402, "RC0402JR-070RL", "Resistor 0 R 0402 (USB D- series)", EQ),
    ("C1", "10uF/16V", "Device:C", C0603, "CL10A106KO8NNNC", "Capacitor 10 uF 16 V X5R 0603 (VBUS)", EQ),
    ("C2", "10uF", "Device:C", C0603, "CL10A106KP8NNNC", "Capacitor 10 uF 10 V X5R 0603 (battery / charger out)", EQ),
    ("C3", "1uF", "Device:C", C0402, "CL05A105KO5NNNC", "Capacitor 1 uF 16 V X5R 0402 (LDO in)", EQ),
    ("C4", "22uF", "Device:C", C0805, "CL21A226MQQNNNE", "Capacitor 22 uF 6.3 V X5R 0805 (LDO out, bulk)", EQ),
    ("C5", "10uF", "Device:C", C0603, "CL10A106KP8NNNC", "Capacitor 10 uF 10 V X5R 0603 (module 3V3)", EQ),
    ("C6", "100nF", "Device:C", C0402, "CL05B104KO5NNNC", "Capacitor 100 nF 16 V X7R 0402 (module 3V3)", EQ),
    ("C7", "100nF", "Device:C", C0402, "CL05B104KO5NNNC", "Capacitor 100 nF 16 V X7R 0402 (LIS2DH12 VDD)", EQ),
    ("C8", "10uF", "Device:C", C0603, "CL10A106KP8NNNC", "Capacitor 10 uF 10 V X5R 0603 (LIS2DH12 VDD bulk)", EQ),
    ("C9", "100nF", "Device:C", C0402, "CL05B104KO5NNNC", "Capacitor 100 nF 16 V X7R 0402 (LIS2DH12 VDD_IO)", EQ),
    ("C10", "10nF", "Device:C", C0402, "CL05B103KB5NNNC", "Capacitor 10 nF 50 V X7R 0402 (ADC filter)", EQ),
    ("C11", "100nF", "Device:C", C0402, "CL05B104KO5NNNC", "Capacitor 100 nF 16 V X7R 0402 (button debounce)", EQ),
    ("TP1", "+3V3", "Connector:TestPoint", TPAD, "", "Test pad 1.0 mm", "No part to fit"),
    ("TP2", "GND", "Connector:TestPoint", TPAD, "", "Test pad 1.0 mm", "No part to fit"),
    ("TP3", "VBAT", "Connector:TestPoint", TPAD, "", "Test pad 1.0 mm", "No part to fit"),
    ("TP4", "UART_TX", "Connector:TestPoint", TPAD, "", "Test pad 1.0 mm", "No part to fit"),
    ("TP5", "UART_RX", "Connector:TestPoint", TPAD, "", "Test pad 1.0 mm", "No part to fit"),
    ("TP6", "EN", "Connector:TestPoint", TPAD, "", "Test pad 1.0 mm", "No part to fit"),
    ("TP7", "BOOT", "Connector:TestPoint", TPAD, "", "Test pad 1.0 mm", "No part to fit"),
]

DATASHEET = {
    "U1": "https://documentation.espressif.com/esp32-h2-wroom-02c_datasheet_en.pdf",
    "U2": "https://www.st.com/resource/en/datasheet/lis2dh12.pdf",
    "U3": "https://www.richtek.com/assets/product_file/RT9080/DS9080-05.pdf",
}

# LED pins: 1 = K, 2 = A.  MOSFET pins: 1 = G, 2 = S, 3 = D.
NETS = {
    "GND": ["J1.A1", "J1.A12", "J1.B1", "J1.B12", "J1.S1", "R1.2", "R2.2", "R3.2", "U4.2", "C1.2", "C2.2",
            "J2.2", "U3.2", "C3.2", "C4.2", "U1.9", "U1.13", "U1.29", "C5.2", "C6.2",
            "U2.5", "U2.6", "U2.7", "U2.8", "C7.2", "C8.2", "C9.2", "U5.2", "Q2.2", "R11.2", "R13.2",
            "C10.2", "SW1.2", "SW2.2", "C11.2", "D1.1", "TP2.1"],
    "VBUS": ["J1.A4", "J1.A9", "J1.B4", "J1.B9", "C1.1", "U4.4", "U5.5", "R4.1"],
    "CC1": ["J1.A5", "R1.1"],
    "CC2": ["J1.B5", "R2.1"],
    "USB_CONN_DP": ["J1.A6", "J1.B6", "U5.1", "U5.6", "R14.1"],
    "USB_DP": ["R14.2", "U1.24"],
    "USB_CONN_DN": ["J1.A7", "J1.B7", "U5.3", "U5.4", "R15.1"],
    "USB_DN": ["R15.2", "U1.23"],
    "CHG_LED_A": ["R4.2", "D2.2"],
    "CHG_STAT": ["D2.1", "U4.1"],
    "CHG_PROG": ["U4.5", "R3.1"],
    "VBAT": ["U4.3", "C2.1", "J2.1", "U3.1", "U3.3", "C3.1", "Q1.2", "R10.1", "TP3.1"],
    "+3V3": ["U3.5", "C4.1", "U1.1", "U1.14", "C5.1", "C6.1", "U2.9", "U2.10", "U2.2", "U2.3",
             "C7.1", "C8.1", "C9.1", "R5.1", "R6.1", "R7.1", "R8.1", "TP1.1"],
    "I2C_SDA": ["U1.3", "U2.4", "R5.2"],
    "I2C_SCL": ["U1.4", "U2.1", "R6.2"],
    "ACC_INT1": ["U1.5", "U2.12"],
    "ACC_INT2": ["U1.6", "U2.11"],
    "IO8_STRAP": ["U1.7", "R8.2"],
    "BOOT": ["U1.8", "R7.2", "SW1.1", "C11.1", "TP7.1"],
    "EN": ["U1.2", "SW2.1", "TP6.1"],
    "LED_STATUS": ["U1.15", "R9.1"],
    "LED_A": ["R9.2", "D1.2"],
    "UART_RX": ["U1.21", "TP5.1"],
    "UART_TX": ["U1.22", "TP4.1"],
    "VBAT_SENSE_EN": ["U1.28", "Q2.1", "R11.1"],
    "VBAT_SENSE": ["U1.27", "R12.2", "R13.1", "C10.1"],
    "Q1_GATE": ["Q1.1", "R10.2", "Q2.3"],
    "VBAT_SW": ["Q1.3", "R12.1"],
}

NC = ["U1.10", "U1.11", "U1.12", "U1.16", "U1.17", "U1.18", "U1.19", "U1.20", "U1.25", "U1.26",
      "U3.4", "J1.A8", "J1.B8"]

# Nets drawn with power symbols (net name -> library symbol).
POWER = {"GND": "power:GND", "+3V3": "power:+3V3", "VBUS": "power:VBUS", "VBAT": "badge:VBAT"}

# ESP32-H2-WROOM-02C pin table: number, name, electrical type (datasheet v1.2, table 2).
U1_PINS = [
    ("1", "3V3", "power_in"), ("2", "EN", "input"), ("3", "IO4/ADC1_CH3", "bidirectional"),
    ("4", "IO5/ADC1_CH4", "bidirectional"), ("5", "IO10", "bidirectional"), ("6", "IO11", "bidirectional"),
    ("7", "IO8", "bidirectional"), ("8", "IO9/BOOT", "bidirectional"), ("9", "GND", "power_in"),
    ("10", "IO12", "bidirectional"), ("11", "IO13/XTAL_32K_P", "bidirectional"),
    ("12", "IO14/XTAL_32K_N", "bidirectional"), ("13", "GND", "power_in"), ("14", "VBAT", "power_in"),
    ("15", "IO22", "bidirectional"), ("16", "NC", "no_connect"), ("17", "NC", "no_connect"),
    ("18", "NC", "no_connect"), ("19", "NC", "no_connect"), ("20", "IO25", "bidirectional"),
    ("21", "RXD0/IO23", "bidirectional"), ("22", "TXD0/IO24", "bidirectional"),
    ("23", "IO26/USB_D-", "bidirectional"), ("24", "IO27/USB_D+", "bidirectional"),
    ("25", "IO3/ADC1_CH2", "bidirectional"), ("26", "IO2/ADC1_CH1", "bidirectional"),
    ("27", "IO1/ADC1_CH0", "bidirectional"), ("28", "IO0", "bidirectional"), ("29", "GND", "power_in"),
]
# Symbol layout: side -> pin numbers in drawing order.
U1_LEFT = ["2", None, "28", "27", "26", "25", "3", "4", "7", "8", "5", "6", "10"]
U1_RIGHT = ["11", "12", "15", "20", "21", "22", "23", "24", None, "16", "17", "18", "19"]
U1_TOP = [("1", -5.08), ("14", 5.08)]
U1_BOTTOM = [("9", -5.08), ("13", 0.0), ("29", 5.08)]
U1_HALF_W, U1_HALF_H, U1_PIN_LEN, U1_ROW0 = 20.32, 20.32, 2.54, 12.7

# -----------------------------------------------------------------------------
# Sheet layout (mm, everything on the 1.27 mm grid).  block: (x, y, w, h, title)
# -----------------------------------------------------------------------------
BLOCKS = {
    "usb": (15.24, 25.4, 106.68, 106.68, "USB-C + ESD"),
    "chg": (127.0, 25.4, 99.06, 106.68, "LiPo charger"),
    "ldo": (231.14, 25.4, 73.66, 53.34, "3V3 LDO"),
    "notes": (231.14, 83.82, 73.66, 48.26, "Design notes"),
    "vsense": (309.88, 25.4, 96.52, 106.68, "Battery sense (switched divider)"),
    "mcu": (15.24, 137.16, 160.02, 109.22, "MCU ESP32-H2-WROOM-02C"),
    "acc": (180.34, 137.16, 104.14, 109.22, "Accelerometer LIS2DH12"),
    "btn": (289.56, 137.16, 68.58, 109.22, "Buttons / LED"),
    "tp": (363.22, 137.16, 43.18, 109.22, "Test points"),
}

# ref: (block, dx, dy, rotation, mirror)
PLACE = {
    "J1": ("usb", 25.4, 50.8, 0, None),
    "R1": ("usb", 63.5, 33.02, 0, None),
    "R2": ("usb", 81.28, 33.02, 0, None),
    "U5": ("usb", 71.12, 63.5, 0, None),
    "R14": ("usb", 71.12, 86.36, 90, None),
    "R15": ("usb", 71.12, 96.52, 90, None),

    "C1": ("chg", 7.62, 38.1, 0, None),
    "U4": ("chg", 48.26, 38.1, 0, None),
    "C2": ("chg", 83.82, 38.1, 0, None),
    "R4": ("chg", 7.62, 68.58, 0, None),
    "D2": ("chg", 7.62, 81.28, 90, None),
    "R3": ("chg", 48.26, 71.12, 0, None),
    "J2": ("chg", 73.66, 71.12, 0, None),

    "C3": ("ldo", 10.16, 22.86, 0, None),
    "U3": ("ldo", 35.56, 22.86, 0, None),
    "C4": ("ldo", 60.96, 22.86, 0, None),

    "R11": ("vsense", 20.32, 58.42, 0, None),
    "Q2": ("vsense", 33.02, 53.34, 0, None),
    "R10": ("vsense", 35.56, 30.48, 0, None),
    "Q1": ("vsense", 53.34, 40.64, 0, "x"),
    "R12": ("vsense", 55.88, 55.88, 0, None),
    "R13": ("vsense", 55.88, 73.66, 0, None),
    "C10": ("vsense", 76.2, 73.66, 0, None),

    "U1": ("mcu", 63.5, 58.42, 0, None),
    "C5": ("mcu", 114.3, 38.1, 0, None),
    "C6": ("mcu", 127.0, 38.1, 0, None),
    "R8": ("mcu", 139.7, 38.1, 0, None),

    "R5": ("acc", 12.7, 33.02, 0, None),
    "R6": ("acc", 27.94, 33.02, 0, None),
    "U2": ("acc", 50.8, 58.42, 0, None),
    "C7": ("acc", 66.04, 33.02, 0, None),
    "C8": ("acc", 78.74, 33.02, 0, None),
    "C9": ("acc", 91.44, 33.02, 0, None),

    "SW1": ("btn", 20.32, 35.56, 0, None),
    "R7": ("btn", 40.64, 33.02, 0, None),
    "C11": ("btn", 53.34, 33.02, 0, None),
    "SW2": ("btn", 20.32, 60.96, 0, None),
    "R9": ("btn", 40.64, 63.5, 0, None),
    "D1": ("btn", 40.64, 78.74, 90, None),

    "TP1": ("tp", 15.24, 30.48, 90, None),
    "TP2": ("tp", 15.24, 40.64, 90, None),
    "TP3": ("tp", 15.24, 58.42, 90, None),
    "TP4": ("tp", 15.24, 68.58, 90, None),
    "TP5": ("tp", 15.24, 76.2, 90, None),
    "TP6": ("tp", 15.24, 83.82, 90, None),
    "TP7": ("tp", 15.24, 91.44, 90, None),
}

# Reference / value text position overrides: ref -> {"ref": (dx, dy), "val": (dx, dy)}
# (offsets from the symbol origin, text left-justified, rotation-0 symbols only).
FIELD_POS = {
    "U1": {"ref": (-20.32, -21.59), "val": (8.89, -21.59)},
    "U2": {"ref": (5.08, -13.97), "val": (5.08, -11.43)},
    "U3": {"ref": (2.54, 7.62), "val": (2.54, 10.16)},
    "U4": {"ref": (2.54, 7.62), "val": (2.54, 10.16)},
    "U5": {"ref": (2.54, 7.62), "val": (2.54, 10.16)},
    "J2": {"ref": (2.54, -3.81), "val": (2.54, 5.08)},
    "SW1": {"ref": (-3.81, -7.62), "val": (-3.81, -5.08)},
    "SW2": {"ref": (-3.81, -7.62), "val": (-3.81, -5.08)},
}

# Label stub length overrides (mm) for pins whose neighbours need the room.
STUB = {"U2.4": 10.16, "U2.1": 10.16, "D2.1": 5.08}

# PWR_FLAG symbols: (net, block, dx, dy).  VBUS and GND enter through passive connector pins.
POWER_FLAGS = [("VBUS", "usb", 88.9, 48.26), ("GND", "usb", 88.9, 76.2)]

# Power pins that get a rotated power symbol directly on the pin (no L-shaped stub).
ROTATED_POWER = {"U2.2", "U2.3"}

# Free text inside the blocks: block -> (dx, dy, text)
NOTES = {
    "usb": (2.54, 101.6,
            "USB 2.0 FS goes to the ESP32-H2 USB-Serial/JTAG pins (GPIO26 = D-, GPIO27 = D+).\n"
            "R1, R2: Rd 5.1k = USB-C sink.  R14, R15: 0R placeholders for EMI tuning."),
    "chg": (2.54, 99.06,
            "Ichg = 1000 V / R3 = 213 mA (R3 = 4.7k): size R3 for <= 0.5 C of the cell.\n"
            "D2 lit = charging (STAT low).  No power-path: the load stays on the battery.\n"
            "J2 = solder pads on the bottom side: pad 1 = BAT+, pad 2 = BAT-.  CHECK THE POLARITY."),
    "ldo": (2.54, 40.64,
            "RT9080-33GJ5: Iq 2 uA, 600 mA.  EN tied to VIN (always on).\n"
            "Pinout 1=VIN 2=GND 3=EN 4=NC 5=VOUT, same as\n"
            "TPS7A0533PDBV and XC6220B331MR-G (alternates)."),
    "vsense": (2.54, 93.98,
               "VBAT_SENSE = VBAT / 2 while VBAT_SENSE_EN (GPIO0) is high; settle >= 3 ms.\n"
               "GPIO0 low or floating (deep sleep): R11 keeps Q2 off, R10 keeps Q1 off,\n"
               "so the divider draws only leakage.  ADC input: GPIO1 = ADC1_CH0."),
    "mcu": (2.54, 96.52,
            "Strapping: GPIO8 pulled up (R8); GPIO9 = BOOT, pulled up (R7), SW1 pulls it low.\n"
            "GPIO25 (JTAG source strap) is unused: it is ignored with the default eFuses.\n"
            "EN: the 10k / 1uF power-up RC is inside the module.  VBAT pin = 3V3 (module default).\n"
            "GPIO10 / GPIO11 / GPIO9 are LP IOs (GPIO7..14) and can wake the chip from deep sleep.\n"
            "Decoupling C5 + C6 right at module pin 1.  Keep the antenna area free of copper."),
    "acc": (2.54, 96.52,
            "I2C address 0x19 (SA0 high).  CS high selects I2C.  RES (pin 5) to GND.\n"
            "INT1 -> GPIO10: FIFO watermark, wakes the MCU.  INT2 -> GPIO11: reserved.\n"
            "The sensor stays powered in sleep (about 2 uA at 1 Hz).  C7 / C9 right at the pins."),
    "btn": (2.54, 96.52,
            "SW1: BOOT / user button (GPIO9, active low).\n"
            "SW2: reset (EN to GND).\n"
            "D1: status LED on GPIO22, active high."),
    "tp": (2.54, 99.06, "1.0 mm pads for pogo pins:\nUART0 console, EN + BOOT\nfor flashing, supplies."),
    "notes": (2.54, 12.7,
              "PCB: 4 layers recommended (sig / GND / PWR / sig), 1.6 mm.\n"
              "U1: antenna at the board edge, no copper on any\n"
              "layer under or beside it (keep-out is in the footprint).\n"
              "Solid GND plane under everything else; stitch vias.\n"
              "USB D+/D-: route as a pair, short, over GND.\n"
              "U2: away from the board edge, buttons and battery\n"
              "connector; no vias or traces under the LGA.\n"
              "Battery leads and C2 close to J2 / U4.\n"
              "This sheet is generated: edit hardware/gen/gen_kicad.py."),
}

# =============================================================================
# 2. S-EXPRESSION HELPERS
# =============================================================================


class Q(str):
    """A quoted string token (escapes are kept exactly as they appear in the file)."""


def q(text):
    return Q(str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n"))


def sx_parse(text):
    stack = [[]]
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
            i += 1
        elif c == ")":
            stack.pop()
            i += 1
        elif c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            stack[-1].append(Q(text[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()":
                j += 1
            stack[-1].append(text[i:j])
            i = j
    return stack[0][0]


def sx_dump(node, indent=0):
    if not isinstance(node, list):
        return '"%s"' % node if isinstance(node, Q) else str(node)
    flat = "(" + " ".join(sx_dump(c) for c in node) + ")"
    if len(flat) <= 110 or all(not isinstance(c, list) for c in node):
        return flat
    pad = "  " * (indent + 1)
    head = []
    rest = list(node)
    while rest and not isinstance(rest[0], list):
        head.append(sx_dump(rest.pop(0)))
    out = "(" + " ".join(head)
    for c in rest:
        out += "\n" + pad + sx_dump(c, indent + 1)
    return out + "\n" + "  " * indent + ")"


def find(node, key):
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None


def findall(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def num(v):
    v = round(float(v), 4)
    if v == 0:
        return "0"
    if v == int(v):
        return str(int(v))
    return ("%.4f" % v).rstrip("0").rstrip(".")


def pt(p):
    return (round(p[0], 4), round(p[1], 4))


_uid_seen = {}


def uid(key):
    _uid_seen[key] = _uid_seen.get(key, 0) + 1
    return str(uuid.uuid5(UUID_NS, "%s#%d" % (key, _uid_seen[key])))


def natural(ref):
    m = re.match(r"([A-Za-z#]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


# =============================================================================
# 3. SYMBOL LIBRARIES
# =============================================================================

FONT = ["effects", ["font", ["size", "1.27", "1.27"]]]


def _prop(name, value, x, y, hide=False, justify=None):
    eff = copy.deepcopy(FONT)
    if justify:
        eff.append(["justify", justify])
    if hide:
        eff.append("hide")
    return ["property", q(name), q(value), ["at", num(x), num(y), "0"], eff]


def _pin(etype, x, y, angle, length, name, number, hide=False):
    p = ["pin", etype, "line", ["at", num(x), num(y), str(angle)], ["length", num(length)]]
    if hide:
        p.append("hide")
    p += [["name", q(name), copy.deepcopy(FONT)], ["number", q(number), copy.deepcopy(FONT)]]
    return p


def build_u1_symbol():
    name = "ESP32-H2-WROOM-02C"
    types = {n: (nm, t) for n, nm, t in U1_PINS}
    pins = []
    for side, order in ((-1, U1_LEFT), (1, U1_RIGHT)):
        y = U1_ROW0
        for number in order:
            if number is not None:
                nm, t = types[number]
                pins.append(_pin(t, side * (U1_HALF_W + U1_PIN_LEN), y, 0 if side < 0 else 180,
                                 U1_PIN_LEN, nm, number))
            y -= 2.54
    for number, x in U1_TOP:
        pins.append(_pin(types[number][1], x, U1_HALF_H + U1_PIN_LEN, 270, U1_PIN_LEN, types[number][0], number))
    for number, x in U1_BOTTOM:
        pins.append(_pin(types[number][1], x, -(U1_HALF_H + U1_PIN_LEN), 90, U1_PIN_LEN, types[number][0], number))
    assert len(pins) == len(U1_PINS) == 29
    body = ["symbol", q(name + "_0_1"),
            ["rectangle", ["start", num(-U1_HALF_W), num(U1_HALF_H)], ["end", num(U1_HALF_W), num(-U1_HALF_H)],
             ["stroke", ["width", "0.254"], ["type", "default"]], ["fill", ["type", "background"]]]]
    return ["symbol", q(name), ["in_bom", "yes"], ["on_board", "yes"],
            _prop("Reference", "U", -20.32, 21.59, justify="left"),
            _prop("Value", name, 8.89, 21.59, justify="left"),
            _prop("Footprint", "badge:ESP32-H2-WROOM-02C", 0, -30.48, hide=True),
            _prop("Datasheet", DATASHEET["U1"], 0, -33.02, hide=True),
            _prop("ki_keywords", "ESP32-H2 802.15.4 Zigbee Thread BLE module", 0, 0, hide=True),
            _prop("ki_description",
                  "Espressif ESP32-H2-WROOM-02C, IEEE 802.15.4 + Bluetooth LE module, PCB antenna, 29 pins", 0, 0,
                  hide=True),
            _prop("ki_fp_filters", "ESP32?H2?WROOM?02C*", 0, 0, hide=True),
            body, ["symbol", q(name + "_1_1")] + pins]


def build_vbat_symbol():
    def line(a, b):
        return ["polyline", ["pts", ["xy", num(a[0]), num(a[1])], ["xy", num(b[0]), num(b[1])]],
                ["stroke", ["width", "0"], ["type", "default"]], ["fill", ["type", "none"]]]
    return ["symbol", q("VBAT"), ["power"], ["pin_names", ["offset", "0"]], ["in_bom", "yes"], ["on_board", "yes"],
            _prop("Reference", "#PWR", 0, -3.81, hide=True),
            _prop("Value", "VBAT", 0, 3.556),
            _prop("Footprint", "", 0, 0, hide=True),
            _prop("Datasheet", "", 0, 0, hide=True),
            _prop("ki_keywords", "global power", 0, 0, hide=True),
            _prop("ki_description", "Power symbol, creates the global net VBAT (Li-Po cell voltage)", 0, 0,
                  hide=True),
            ["symbol", q("VBAT_0_1"), line((0, 0), (0, 2.54)), line((-0.762, 1.27), (0, 2.54)),
             line((0, 2.54), (0.762, 1.27))],
            ["symbol", q("VBAT_1_1"), _pin("power_in", 0, 0, 90, 0, "VBAT", "1", hide=True)]]


LOCAL_SYMBOLS = {"ESP32-H2-WROOM-02C": build_u1_symbol, "VBAT": build_vbat_symbol}
_lib_cache = {}


def load_lib(lib):
    if lib not in _lib_cache:
        if lib == "badge":
            _lib_cache[lib] = {n: f() for n, f in LOCAL_SYMBOLS.items()}
        else:
            path = os.path.join(SYM_DIR, lib + ".kicad_sym")
            if not os.path.exists(path):
                sys.exit("KiCad symbol library not found: %s (set KICAD_SYMBOL_DIR)" % path)
            tree = sx_parse(open(path, encoding="utf-8").read())
            _lib_cache[lib] = {str(s[1]): s for s in findall(tree, "symbol")}
    return _lib_cache[lib]


def flat_symbol(lib, name):
    """Return a stand-alone copy of a library symbol ('extends' resolved)."""
    sym = load_lib(lib)[name]
    ext = find(sym, "extends")
    if ext is None:
        return copy.deepcopy(sym)
    parent_name = str(ext[1])
    parent = flat_symbol(lib, parent_name)
    child_props = {str(p[1]): p for p in findall(sym, "property")}
    out, tail = ["symbol", q(name)], []
    for c in parent[2:]:
        if isinstance(c, list) and c[0] == "property":
            out.append(copy.deepcopy(child_props.pop(str(c[1]), c)))
        elif isinstance(c, list) and c[0] == "symbol":
            sub = copy.deepcopy(c)
            sub[1] = Q(name + str(sub[1])[len(parent_name):])
            tail.append(sub)
        else:
            out.append(c)
    return out + [copy.deepcopy(p) for p in child_props.values()] + tail


def symbol_pins(sym):
    pins = {}
    for sub in findall(sym, "symbol"):
        for p in findall(sub, "pin"):
            at = find(p, "at")
            pins[str(find(p, "number")[1])] = {
                "name": str(find(p, "name")[1]), "type": p[1], "x": float(at[1]), "y": float(at[2]),
                "angle": int(float(at[3])) if len(at) > 3 else 0, "length": float(find(p, "length")[1]),
            }
    return pins


def symbol_prop(sym, name):
    for p in findall(sym, "property"):
        if str(p[1]) == name:
            at = find(p, "at")
            eff = find(p, "effects") or []
            just = find(eff, "justify")
            return {"value": str(p[2]), "x": float(at[1]), "y": float(at[2]),
                    "angle": int(float(at[3])) if len(at) > 3 else 0,
                    "justify": [t for t in just[1:]] if just else [], "hide": "hide" in eff}
    return None


# =============================================================================
# 4. SCHEMATIC GENERATION
# =============================================================================

MATRIX = {0: (1, 0, 0, -1), 90: (0, -1, -1, 0), 180: (-1, 0, 0, 1), 270: (0, 1, 1, 0)}
UP, DOWN, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)
CHAR_W = 1.05  # estimated advance of one 1.27 mm character


def transform(rot, mirror):
    x1, y1, x2, y2 = MATRIX[rot]
    if mirror == "x":
        assert rot == 0
        return (x1, y1, x2, -y2)
    if mirror == "y":
        assert rot == 0
        return (-x1, y1, x2, y2)
    return (x1, y1, x2, y2)


def apply(m, x, y):
    return (m[0] * x + m[1] * y, m[2] * x + m[3] * y)


class Sheet:
    def __init__(self):
        self.root = str(uuid.uuid5(UUID_NS, "root-sheet"))
        self.items = []          # raw s-expression strings
        self.wires = []          # [(p, q)]
        self.used_symbols = {}   # "Lib:Name" -> flattened definition
        self.endpoints = {}      # "REF.PIN" -> (point, outward direction)
        self.pin_points = {}     # point -> set of "REF.PIN"
        self.nc_points = []
        self.label_count = 0
        self.pwr_count = 0
        self.flg_count = 0
        self.done = set()        # pins already wired by hand
        self.attached = set()    # (net, point) already carrying a label / power symbol

    # ---- library access ---------------------------------------------------
    def lib_symbol(self, lib_id):
        if lib_id not in self.used_symbols:
            lib, name = lib_id.split(":")
            self.used_symbols[lib_id] = flat_symbol(lib, name)
        return self.used_symbols[lib_id]

    # ---- primitives -------------------------------------------------------
    def wire(self, a, b):
        a, b = pt(a), pt(b)
        if a != b:
            self.wires.append((a, b))

    def path(self, *points):
        for a, b in zip(points, points[1:]):
            self.wire(a, b)

    def label(self, net, p, angle=0):
        p = pt(p)
        just = "right" if angle in (180, 270) else "left"
        self.items.append(
            '(label "%s" (at %s %s %d) (effects (font (size 1.27 1.27)) (justify %s bottom)) (uuid %s))'
            % (q(net), num(p[0]), num(p[1]), angle, just, uid("label:" + net)))
        self.label_count += 1

    def no_connect(self, p):
        p = pt(p)
        self.nc_points.append(p)
        self.items.append("(no_connect (at %s %s) (uuid %s))" % (num(p[0]), num(p[1]), uid("nc")))

    def text(self, s, p, size=1.27, bold=False):
        font = "(font (size %s %s)%s)" % (num(size), num(size), " (thickness %s) bold" % num(size / 5) if bold else "")
        self.items.append('(text "%s" (at %s %s 0) (effects %s (justify left bottom)) (uuid %s))'
                          % (q(s), num(p[0]), num(p[1]), font, uid("text")))

    def rect(self, x, y, w, h):
        self.items.append("(rectangle (start %s %s) (end %s %s) (stroke (width 0.2) (type dash)) "
                          "(fill (type none)) (uuid %s))" % (num(x), num(y), num(x + w), num(y + h), uid("rect")))

    @staticmethod
    def _field(name, value, p, angle=0, justify=None, hide=False):
        eff = "(effects (font (size 1.27 1.27))%s%s)" % (" (justify %s)" % justify if justify else "",
                                                        " hide" if hide else "")
        return '(property "%s" "%s" (at %s %s %d) %s)' % (q(name), q(value), num(p[0]), num(p[1]), angle, eff)

    def _symbol(self, lib_id, ref, p, rot, mirror, fields, in_bom=True):
        pins = symbol_pins(self.lib_symbol(lib_id))
        out = ["(symbol (lib_id \"%s\") (at %s %s %d)%s (unit 1)" % (
            q(lib_id), num(p[0]), num(p[1]), rot, " (mirror %s)" % mirror if mirror else ""),
            "  (in_bom %s) (on_board yes) (dnp no)" % ("yes" if in_bom else "no"),
            "  (uuid %s)" % uid("sym:" + ref)]
        out += ["  " + f for f in fields]
        out += ['  (pin "%s" (uuid %s))' % (q(n), uid("pin:%s.%s" % (ref, n))) for n in sorted(pins, key=str)]
        out.append('  (instances (project "%s" (path "/%s" (reference "%s") (unit 1))))' % (PROJECT, self.root, q(ref)))
        out.append(")")
        self.items.append("\n".join(out))

    # ---- power symbols ----------------------------------------------------
    def power(self, net, p, direction):
        """Place the power symbol of `net` with its pin on p, pointing in `direction`."""
        p = pt(p)
        lib_id = POWER[net]
        natural_dir = DOWN if net == "GND" else UP
        rot = {(UP, UP): 0, (UP, LEFT): 90, (UP, DOWN): 180, (UP, RIGHT): 270,
               (DOWN, DOWN): 0, (DOWN, RIGHT): 90, (DOWN, UP): 180, (DOWN, LEFT): 270}[(natural_dir, direction)]
        sym = self.lib_symbol(lib_id)
        val = symbol_prop(sym, "Value")
        if rot == 0:
            vp, va = (p[0] + val["x"], p[1] - val["y"]), 0
        elif direction in (LEFT, RIGHT):
            dist = 2.54 + 0.9 + len(net) * CHAR_W / 2
            vp, va = (p[0] + direction[0] * dist, p[1]), 90
        else:
            vp, va = (p[0], p[1] + direction[1] * 3.81), 0
        self.pwr_count += 1
        ref = "#PWR%03d" % self.pwr_count
        fields = [self._field("Reference", ref, p, hide=True), self._field("Value", net, vp, va),
                  self._field("Footprint", "", p, hide=True), self._field("Datasheet", "", p, hide=True)]
        self._symbol(lib_id, ref, p, rot, None, fields)

    def power_flag(self, net, p):
        """PWR_FLAG on a net that no power-output pin drives (keeps the KiCad GUI ERC quiet)."""
        a = pt(p)
        b = pt((a[0] + 7.62, a[1]))
        self.wire(a, b)
        self.power(net, a, DOWN if net == "GND" else UP)
        val = symbol_prop(self.lib_symbol("power:PWR_FLAG"), "Value")
        self.flg_count += 1
        ref = "#FLG%02d" % self.flg_count
        fields = [self._field("Reference", ref, b, hide=True),
                  self._field("Value", "PWR_FLAG", (b[0] + val["x"], b[1] - val["y"])),
                  self._field("Footprint", "", b, hide=True), self._field("Datasheet", "~", b, hide=True)]
        self._symbol("power:PWR_FLAG", ref, b, 0, None, fields)

    # ---- parts ------------------------------------------------------------
    def place_part(self, part):
        ref, value, lib_id, footprint, mpn = part[0], part[1], part[2], part[3], part[4]
        block, dx, dy, rot, mirror = PLACE[ref]
        bx, by = BLOCKS[block][0], BLOCKS[block][1]
        x, y = pt((bx + dx, by + dy))
        sym = self.lib_symbol(lib_id)
        m = transform(rot, mirror)
        for number, pin in symbol_pins(sym).items():
            ox, oy = apply(m, pin["x"], pin["y"])
            a = pin["angle"]
            lib_out = {0: (-1, 0), 90: (0, -1), 180: (1, 0), 270: (0, 1)}[a]
            d = apply(m, *lib_out)
            p = pt((x + ox, y + oy))
            self.endpoints["%s.%s" % (ref, number)] = (p, (int(d[0]), int(d[1])))
            self.pin_points.setdefault(p, set()).add("%s.%s" % (ref, number))

        kind = re.match(r"[A-Z]+", ref).group(0)
        wr, wv = len(ref) * CHAR_W, len(value) * CHAR_W
        hide_val = False
        if ref in FIELD_POS:
            assert rot == 0 and not mirror
            (rx, ry), (vx, vy) = FIELD_POS[ref]["ref"], FIELD_POS[ref]["val"]
            rf = ((x + rx, y + ry), 0, "left")
            vf = ((x + vx, y + vy), 0, "left")
        elif kind in ("R", "C") and rot == 0:
            rf = ((x + 2.54, y - 1.27), 0, "left")
            vf = ((x + 2.54, y + 1.27), 0, "left")
        elif kind == "R" and rot == 90:
            rf = ((x - 0.6 - wr / 2, y - 2.54), 90, None)
            vf = ((x + 0.6 + wv / 2, y - 2.54), 90, None)
        elif kind == "D" and rot == 90:
            rf = ((x + 3.2 + wr / 2, y - 1.27), 90, None)
            vf = ((x + 3.2 + wv / 2, y + 1.27), 90, None)
        elif kind == "TP":
            rf = ((x - 5.6 - wr / 2, y), 90, None)
            vf = ((x, y), 90, None)
            hide_val = True
        elif mirror:  # centred text is immune to KiCad's justification flipping on mirrored symbols
            rf = ((x + 5.08 + wr / 2, y - 1.27), 0, None)
            vf = ((x + 5.08 + wv / 2, y + 1.27), 0, None)
        else:
            assert rot == 0
            lr, lv = symbol_prop(sym, "Reference"), symbol_prop(sym, "Value")
            jr = [j for j in lr["justify"] if j in ("left", "right")]
            jv = [j for j in lv["justify"] if j in ("left", "right")]
            rf = ((x + lr["x"], y - lr["y"]), lr["angle"], jr[0] if jr else None)
            vf = ((x + lv["x"], y - lv["y"]), lv["angle"], jv[0] if jv else None)
        ds = DATASHEET.get(ref) or (symbol_prop(sym, "Datasheet") or {}).get("value", "~")
        fields = [self._field("Reference", ref, *rf), self._field("Value", value, *vf, hide=hide_val),
                  self._field("Footprint", footprint, (x, y), hide=True),
                  self._field("Datasheet", ds, (x, y), hide=True)]
        if mpn:
            fields.append(self._field("MPN", mpn, (x, y), hide=True))
        self._symbol(lib_id, ref, (x, y), rot, mirror, fields)

    # ---- connections ------------------------------------------------------
    def ep(self, pin):
        return self.endpoints[pin][0]

    def attach(self, net, pin):
        """Default connection style of one pin: power symbol, or short stub + net label."""
        p, d = self.endpoints[pin]
        if pin in self.done or (net, p) in self.attached:
            return
        self.attached.add((net, p))
        if net in POWER:
            pref = DOWN if net == "GND" else UP
            if d == pref or pin in ROTATED_POWER or d[0] == 0:
                self.power(net, p, d)
            else:
                p1 = (p[0] + d[0] * 2.54, p[1])
                p2 = (p1[0], p1[1] + pref[1] * 2.54)
                self.path(p, p1, p2)
                self.power(net, p2, pref)
        else:
            length = STUB.get(pin, 2.54)
            e = (p[0] + d[0] * length, p[1] + d[1] * length)
            self.wire(p, e)
            self.label(net, e, 180 if d == LEFT else 0)

    def custom_wiring(self):
        """Connections that read better as real wires than as labels."""
        ep, done = self.ep, self.done

        def link(child, parent):
            self.wire(ep(child), ep(parent))
            done.add(child)

        link("J1.B7", "J1.A7")       # the two D- contacts
        link("J1.B6", "J1.A6")       # the two D+ contacts
        link("U3.3", "U3.1")         # LDO EN tied to VIN
        link("U2.10", "U2.9")        # VDD_IO tied to VDD

        # charge LED: VBUS - R4 - D2 - STAT
        a, b = ep("R4.2"), ep("D2.2")
        self.wire(a, b)
        self.label("CHG_LED_A", (a[0], (a[1] + b[1]) / 2), 0)
        done.update(("R4.2", "D2.2"))

        # status LED: GPIO22 - R9 - D1 - GND
        a, b = ep("R9.2"), ep("D1.2")
        self.wire(a, b)
        self.label("LED_A", (a[0], (a[1] + b[1]) / 2), 0)
        done.update(("R9.2", "D1.2"))

        # battery sense: Q1 gate node (R10 pull-up, Q2 drain)
        g, r10, q2d = ep("Q1.1"), ep("R10.2"), ep("Q2.3")
        assert r10[0] == q2d[0]
        node = (r10[0], g[1])
        self.path(r10, node, g)
        self.wire(q2d, node)
        self.label("Q1_GATE", (g[0] - 1.27, g[1]), 180)
        done.update(("Q1.1", "R10.2", "Q2.3"))

        # Q2 gate: GPIO0 with R11 pull-down
        g2, r11 = ep("Q2.1"), ep("R11.1")
        corner = (r11[0], g2[1])
        self.path(r11, corner, g2)
        self.label("VBAT_SENSE_EN", corner, 180)
        done.update(("Q2.1", "R11.1"))

        # switched divider: Q1 drain - R12 - (VBAT_SENSE) - R13 - GND, C10 across R13
        d1, r12a, r12b, r13a, c10 = ep("Q1.3"), ep("R12.1"), ep("R12.2"), ep("R13.1"), ep("C10.1")
        assert d1[0] == r12a[0] == r13a[0]
        self.wire(d1, r12a)
        self.label("VBAT_SW", (d1[0], round((d1[1] + r12a[1]) / 2 / 1.27) * 1.27), 0)
        self.wire(r12b, r13a)
        tap = (r12b[0], round((r12b[1] + r13a[1]) / 2 / 1.27) * 1.27)
        self.path(c10, (c10[0], tap[1]), tap)
        self.label("VBAT_SENSE", (tap[0] + 2.54, tap[1]), 0)
        done.update(("Q1.3", "R12.1", "R12.2", "R13.1", "C10.1"))

    # ---- finishing --------------------------------------------------------
    def finish_wires(self):
        """Split wires at T-connections, emit them, and add junction dots."""
        changed = True
        while changed:
            changed = False
            ends = {e for w in self.wires for e in w}
            for i, (a, b) in enumerate(self.wires):
                for e in ends:
                    if e in (a, b):
                        continue
                    on_v = a[0] == b[0] == e[0] and min(a[1], b[1]) < e[1] < max(a[1], b[1])
                    on_h = a[1] == b[1] == e[1] and min(a[0], b[0]) < e[0] < max(a[0], b[0])
                    if on_v or on_h:
                        self.wires[i:i + 1] = [(a, e), (e, b)]
                        changed = True
                        break
                if changed:
                    break
        count = {}
        for a, b in self.wires:
            self.items.append("(wire (pts (xy %s %s) (xy %s %s)) (stroke (width 0) (type default)) (uuid %s))"
                              % (num(a[0]), num(a[1]), num(b[0]), num(b[1]), uid("wire")))
            for e in (a, b):
                count[e] = count.get(e, 0) + 1
        for p, c in sorted(count.items()):
            if c + (1 if p in self.pin_points else 0) >= 3:
                self.items.append("(junction (at %s %s) (diameter 0) (color 0 0 0 0) (uuid %s))"
                                  % (num(p[0]), num(p[1]), uid("junction")))

    def render(self):
        libs = "\n".join("    " + sx_dump(_with_lib_name(lib_id, sym), 2)
                         for lib_id, sym in sorted(self.used_symbols.items()))
        head = ("(kicad_sch (version 20230121) (generator eeschema)\n\n  (uuid %s)\n\n  (paper \"A3\")\n\n"
                "  (title_block\n    (title \"%s\")\n    (date \"%s\")\n    (rev \"%s\")\n"
                "    (company \"haxeko/h2-zigbee-lis2dh12\")\n"
                "    (comment 1 \"Generated by hardware/gen/gen_kicad.py - edit the generator, not this file\")\n"
                "    (comment 2 \"Wearable worker-activity badge: Zigbee sleepy end device\")\n  )\n\n"
                "  (lib_symbols\n%s\n  )\n\n" % (self.root, q(TITLE), DATE, REV, libs))
        body = "\n".join("  " + it.replace("\n", "\n  ") for it in self.items)
        tail = '\n\n  (sheet_instances\n    (path "/" (page "1"))\n  )\n)\n'
        return head + body + tail


def _with_lib_name(lib_id, sym):
    out = copy.deepcopy(sym)
    out[1] = Q(lib_id)
    return out


def build_sheet():
    sh = Sheet()
    for name, (x, y, w, h, title) in BLOCKS.items():
        sh.rect(x, y, w, h)
        sh.text(title, (x + 2.54, y + 6.35), size=2.54, bold=True)
        if name in NOTES:
            dx, dy, s = NOTES[name]
            lines = s.count("\n") + 1
            sh.text(s, (x + dx, y + dy + (lines - 1) * 2.0), size=1.27)
    for part in PARTS:
        sh.place_part(part)
    sh.custom_wiring()
    for net, pins in NETS.items():
        for pin in pins:
            sh.attach(net, pin)
    for pin in NC:
        sh.no_connect(sh.ep(pin))
    for net, block, dx, dy in POWER_FLAGS:
        sh.power_flag(net, (BLOCKS[block][0] + dx, BLOCKS[block][1] + dy))
    sh.finish_wires()
    return sh


# =============================================================================
# 5. FOOTPRINT, PROJECT FILES, BOM
# =============================================================================

def u1_pads():
    """(number, x, y, w, h) from the datasheet land pattern; origin = module centre, antenna up."""
    pads = []
    for i in range(9):
        pads.append((str(1 + i), -8.75, -2.9 + 1.5 * i, 1.5, 0.9))
    for k in range(10):
        pads.append((str(10 + k), -6.75 + 1.5 * k, 9.75, 0.9, 1.5))
    for j in range(9):
        pads.append((str(20 + j), 8.75, 9.1 - 1.5 * j, 1.5, 0.9))
    pads.append(("29", -0.47, 2.01, 2.8, 0.55))
    pads.append(("29", -0.47, 3.73, 2.8, 1.6))
    return pads


def build_footprint():
    name = "ESP32-H2-WROOM-02C"

    def ts(key):
        return "(tstamp %s)" % uid("fp:" + key)

    def line(a, b, layer, width):
        return ("  (fp_line (start %s %s) (end %s %s)\n    (stroke (width %s) (type solid)) (layer \"%s\") %s)"
                % (num(a[0]), num(a[1]), num(b[0]), num(b[1]), num(width), layer, ts("line")))

    def poly(points, layer, width):
        return [line(a, b, layer, width) for a, b in zip(points, points[1:] + points[:1])]

    def text(kind, s, x, y, layer, size=1.0):
        return ("  (fp_text %s \"%s\" (at %s %s) (layer \"%s\")\n      (effects (font (size %s %s) (thickness %s)))\n"
                "    %s\n  )" % (kind, q(s), num(x), num(y), layer, num(size), num(size), num(size * 0.15), ts("text")))

    out = ['(footprint "%s" (version 20221018) (generator pcbnew)' % name,
           '  (layer "F.Cu")',
           '  (descr "Espressif ESP32-H2-WROOM-02C, 18 x 20 x 3.2 mm IEEE 802.15.4 / Bluetooth LE module with PCB '
           'antenna, land pattern from datasheet v1.2 figure 7, %s")' % DATASHEET["U1"],
           '  (tags "ESP32-H2 WROOM-02C espressif zigbee thread 802.15.4 module")',
           "  (attr smd)",
           text("reference", "REF**", 0, -11.6, "F.SilkS"),
           text("value", name, 0, 12.2, "F.Fab"),
           text("user", "${REFERENCE}", 0, -1.0, "F.Fab"),
           text("user", "Antenna keep-out: no copper on any layer", 0, -6.4, "Cmts.User", 0.6),
           text("user", "ANTENNA", 0, -8.6, "F.Fab", 0.8)]
    # fabrication outline with pin-1 chamfer, antenna boundary
    out += poly([(-9, -10), (9, -10), (9, 10), (-9, 10)], "F.Fab", 0.1)
    out.append(line((-9, -4), (9, -4), "F.Fab", 0.1))
    # silkscreen: antenna end, sides down to the first pads, bottom corners, pin-1 mark
    for seg in [((-9.12, -10.12), (9.12, -10.12)), ((-9.12, -10.12), (-9.12, -3.7)), ((9.12, -10.12), (9.12, -3.7)),
                ((-9.12, 10.12), (-7.6, 10.12)), ((-9.12, 10.12), (-9.12, 9.9)),
                ((9.12, 10.12), (7.6, 10.12)), ((9.12, 10.12), (9.12, 9.9)),
                ((-9.8, -3.7), (-9.12, -3.7))]:
        out.append(line(seg[0], seg[1], "F.SilkS", 0.12))
    out += poly([(-9.75, -10.75), (9.75, -10.75), (9.75, 10.75), (-9.75, 10.75)], "F.CrtYd", 0.05)
    for number, x, y, w, h in u1_pads():
        paste = '"F.Cu" "F.Paste" "F.Mask"'
        out.append('  (pad "%s" smd rect (at %s %s) (size %s %s) (layers %s) %s)'
                   % (number, num(x), num(y), num(w), num(h), paste, ts("pad" + number)))
    out.append(
        '  (zone (net 0) (net_name "") (layers "*.Cu") %s (name "Antenna keep-out") (hatch full 0.508)\n'
        "    (connect_pads (clearance 0))\n"
        "    (min_thickness 0.254) (filled_areas_thickness no)\n"
        "    (keepout (tracks not_allowed) (vias not_allowed) (pads not_allowed) (copperpour not_allowed) "
        "(footprints allowed))\n"
        "    (fill (thermal_gap 0.508) (thermal_bridge_width 0.508))\n"
        "    (polygon\n      (pts\n        (xy -9.5 -10.5)\n        (xy 9.5 -10.5)\n        (xy 9.5 -4)\n"
        "        (xy -9.5 -4)\n      )\n    )\n  )" % ts("zone"))
    out.append(")")
    return "\n".join(out) + "\n"


# PCB design rules: 4-layer board, 0.15 mm track / space, vias 0.6 mm with a 0.3 mm hole.
# Shared with gen_pcb.py, which lays the board out to these numbers.
PCB_TRACK = {"signal": 0.15, "power": 0.3}
PCB_RULES = {
    "allow_blind_buried_vias": False, "allow_microvias": False, "max_error": 0.005,
    "min_clearance": 0.15, "min_connection": 0.0, "min_copper_edge_clearance": 0.3,
    "min_hole_clearance": 0.2, "min_hole_to_hole": 0.25,
    "min_microvia_diameter": 0.2, "min_microvia_drill": 0.1, "min_resolved_spokes": 1,
    "min_silk_clearance": 0.0, "min_text_height": 0.6, "min_text_thickness": 0.08,
    "min_through_hole_diameter": 0.3, "min_track_width": 0.15, "min_via_annular_width": 0.15,
    "min_via_diameter": 0.6, "solder_mask_clearance": 0.0, "solder_mask_min_width": 0.0,
    "solder_mask_to_copper_clearance": 0.0, "use_height_for_length_calcs": True,
}
# The footprint-library checks need the user's global library table, which a
# scripted run does not have; everything else keeps KiCad's default severity.
PCB_RULE_SEVERITIES = {"lib_footprint_issues": "ignore", "lib_footprint_mismatch": "ignore"}


def build_battery_footprint():
    """Two SMD pads for the LiPo leads. Placed on the bottom side by gen_pcb.py."""
    name = "BatteryPads_1x02_P4.0mm"

    def ts(key):
        return "(tstamp %s)" % uid("fpbat:" + key)

    out = ['(footprint "%s" (version 20221018) (generator gen_kicad) (layer "F.Cu")' % name,
           '  (descr "Solder pads for LiPo battery leads, 2.0 x 3.0 mm, 4.0 mm pitch. Pad 1 = +, pad 2 = -")',
           '  (tags "battery wire solder pads")', "  (attr smd exclude_from_pos_files exclude_from_bom)",
           '  (fp_text reference "REF**" (at 0 -2.4) (layer "F.SilkS") %s\n'
           "    (effects (font (size 0.8 0.8) (thickness 0.12))))" % ts("ref"),
           '  (fp_text value "%s" (at 0 2.6) (layer "F.Fab") %s\n'
           "    (effects (font (size 0.8 0.8) (thickness 0.12))))" % (name, ts("val")),
           '  (fp_text user "+" (at -2 -2.3) (layer "F.SilkS") %s\n'
           "    (effects (font (size 1 1) (thickness 0.15))))" % ts("plus"),
           '  (fp_text user "-" (at 2 -2.3) (layer "F.SilkS") %s\n'
           "    (effects (font (size 1 1) (thickness 0.15))))" % ts("minus")]
    for (x0, y0, x1, y1, key) in ((-3.25, -1.75, 3.25, -1.75, "c1"), (3.25, -1.75, 3.25, 1.75, "c2"),
                                  (3.25, 1.75, -3.25, 1.75, "c3"), (-3.25, 1.75, -3.25, -1.75, "c4")):
        out.append('  (fp_line (start %s %s) (end %s %s)\n    (stroke (width 0.05) (type solid)) (layer "F.CrtYd") %s)'
                   % (num(x0), num(y0), num(x1), num(y1), ts(key)))
    for number, x in (("1", -2.0), ("2", 2.0)):
        out.append('  (pad "%s" smd roundrect (at %s 0) (size 2 3) (layers "F.Cu" "F.Mask") '
                   "(roundrect_rratio 0.15) %s)" % (number, num(x), ts("pad" + number)))
    out.append(")")
    return "\n".join(out) + "\n"


def project_json(root_uuid):
    def netclass(name, track, clearance=PCB_RULES["min_clearance"]):
        return {"bus_width": 12, "clearance": clearance, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                "name": name, "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
                "track_width": track, "via_diameter": PCB_RULES["min_via_diameter"],
                "via_drill": PCB_RULES["min_through_hole_diameter"], "wire_width": 6}
    return {
        "board": {"3dviewports": [],
                  "design_settings": {"defaults": {}, "diff_pair_dimensions": [], "drc_exclusions": [],
                                      "rule_severities": PCB_RULE_SEVERITIES, "rules": PCB_RULES,
                                      "track_widths": [0.0, PCB_TRACK["signal"], PCB_TRACK["power"]],
                                      "via_dimensions": [{"diameter": 0.0, "drill": 0.0}]},
                  "layer_presets": [], "viewports": []},
        "boards": [],
        "cvpcb": {"equivalence_files": []},
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": PROJECT + ".kicad_pro", "version": 1},
        "net_settings": {
            "classes": [netclass("Default", PCB_TRACK["signal"]), netclass("Power", PCB_TRACK["power"])],
            "meta": {"version": 3},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [{"netclass": "Power", "pattern": p} for p in ("+3V3", "VBAT", "VBUS", "GND")],
        },
        "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [[root_uuid, ""]],
        "text_variables": {},
    }


def write_bom(path):
    groups = {}
    for ref, value, _sym, footprint, mpn, descr, notes in PARTS:
        key = (value, footprint, mpn, re.sub(r" \(.*\)$", "", descr), notes) if ref[0] in "RC" else \
              (value, footprint, mpn, descr, notes)
        groups.setdefault(key, {"refs": [], "descr": []})
        groups[key]["refs"].append(ref)
        groups[key]["descr"].append(descr)
    rows = []
    for (value, footprint, mpn, descr, notes), g in groups.items():
        refs = sorted(g["refs"], key=natural)
        rows.append((natural(refs[0]), [", ".join(refs), len(refs), value, descr, footprint, mpn, notes]))
    order = {"U": 0, "Q": 1, "D": 2, "J": 3, "SW": 4, "R": 5, "C": 6, "TP": 7}
    rows.sort(key=lambda r: (order.get(r[0][0], 9), r[0][1]))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Ref", "Qty", "Value", "Description", "Footprint", "MPN", "Notes"])
        for _key, row in rows:
            w.writerow(row)
    return len(rows), sum(r[1][1] for r in rows)


# =============================================================================
# 6. VERIFICATION
# =============================================================================

def check_design():
    """Every pin of every symbol must be in exactly one net or in the NC list."""
    errors = []
    refs = {p[0]: p for p in PARTS}
    seen = {}
    for net, pins in NETS.items():
        for pin in pins:
            if pin in seen:
                errors.append("%s is in both %s and %s" % (pin, seen[pin], net))
            seen[pin] = net
    for pin in NC:
        if pin in seen:
            errors.append("%s is NC but also in net %s" % (pin, seen[pin]))
        seen[pin] = "<NC>"
    for ref, part in refs.items():
        lib, name = part[2].split(":")
        for number in symbol_pins(flat_symbol(lib, name)):
            if "%s.%s" % (ref, number) not in seen:
                errors.append("%s.%s is not assigned to a net or NC" % (ref, number))
    for pin in seen:
        ref, number = pin.split(".")
        if ref not in refs:
            errors.append("unknown reference in %s" % pin)
            continue
        lib, name = refs[ref][2].split(":")
        if number not in symbol_pins(flat_symbol(lib, name)):
            errors.append("%s: symbol %s has no pin %s" % (pin, refs[ref][2], number))
    if set(PLACE) != set(refs):
        errors.append("PLACE and PARTS differ: %s" % sorted(set(PLACE) ^ set(refs)))
    return errors


def check_footprints():
    errors = []
    for ref, _value, sym_id, footprint, *_ in PARTS:
        lib, name = footprint.split(":")
        base = os.path.join(KICAD_DIR, "badge.pretty") if lib == "badge" else os.path.join(FP_DIR, lib + ".pretty")
        path = os.path.join(base, name + ".kicad_mod")
        if not os.path.exists(path):
            errors.append("%s: footprint %s not found (%s)" % (ref, footprint, path))
            continue
        pads = {str(p[1]) for p in findall(sx_parse(open(path, encoding="utf-8").read()), "pad")}
        slib, sname = sym_id.split(":")
        missing = set(symbol_pins(flat_symbol(slib, sname))) - pads
        if missing:
            errors.append("%s: footprint %s lacks pads %s" % (ref, footprint, sorted(missing)))
    return errors


def check_netlist(net_path, sheet):
    errors = []
    tree = sx_parse(open(net_path, encoding="utf-8").read())
    exported, node_net = {}, {}
    for net in findall(find(tree, "nets"), "net"):
        name = str(find(net, "name")[1])
        nodes = {(str(find(nd, "ref")[1]), str(find(nd, "pin")[1])) for nd in findall(net, "node")}
        exported[name] = nodes
        for nd in nodes:
            node_net[nd] = name
    split = lambda pin: tuple(pin.split("."))
    for net, pins in NETS.items():
        want = {split(p) for p in pins}
        names = {node_net.get(nd) for nd in want}
        if None in names:
            errors.append("net %s: pins missing from the exported netlist: %s"
                          % (net, sorted(nd for nd in want if nd not in node_net)))
            continue
        if len(names) != 1:
            errors.append("net %s is split into %s" % (net, sorted(names)))
            continue
        got = names.pop()
        if exported[got] != want:
            errors.append("net %s (exported as %s) has extra pins %s" % (net, got, sorted(exported[got] - want)))
        expect = net if net in POWER else "/" + net
        if got != expect:
            errors.append("net %s is exported under the name %s" % (net, got))
    for pin in NC:
        nd = split(pin)
        got = node_net.get(nd)
        if got is None:
            errors.append("NC pin %s missing from the exported netlist" % pin)
        elif exported[got] != {nd} or not got.startswith("unconnected-"):
            errors.append("NC pin %s is connected: net %s %s" % (pin, got, sorted(exported[got])))
        if sheet.ep(pin) not in sheet.nc_points:
            errors.append("NC pin %s has no no-connect flag" % pin)
    known = {split(p) for pins in NETS.values() for p in pins} | {split(p) for p in NC}
    for nd in node_net:
        if nd not in known:
            errors.append("unexpected node %s.%s in net %s" % (nd[0], nd[1], node_net[nd]))
    comps = {str(find(c, "ref")[1]): c for c in findall(find(tree, "components"), "comp")}
    for ref, value, _sym, footprint, *_ in PARTS:
        c = comps.pop(ref, None)
        if c is None:
            errors.append("component %s missing from the exported netlist" % ref)
        elif str(find(c, "value")[1]) != value or str(find(c, "footprint")[1]) != footprint:
            errors.append("component %s: value/footprint differ in the exported netlist" % ref)
    for ref in comps:
        errors.append("unexpected component %s in the exported netlist" % ref)
    # the schematic file itself must carry the no-connect flags
    sch = sx_parse(open(os.path.join(KICAD_DIR, PROJECT + ".kicad_sch"), encoding="utf-8").read())
    flags = {pt((float(find(n, "at")[1]), float(find(n, "at")[2]))) for n in findall(sch, "no_connect")}
    for pin in NC:
        if sheet.ep(pin) not in flags:
            errors.append("no-connect flag for %s not found in the schematic file" % pin)
    return errors, len(exported)


# =============================================================================
# 7. MAIN
# =============================================================================

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        print(r.stdout)
        sys.exit("command failed: " + " ".join(cmd))
    return r.stdout


def main():
    results = []

    def report(name, errors, detail=""):
        results.append((name, errors))
        print("%-4s %s%s" % ("PASS" if not errors else "FAIL", name, (" - " + detail) if detail and not errors else ""))
        for e in errors:
            print("       " + e)

    report("design data (every pin in exactly one net or NC)", check_design(),
           "%d parts, %d nets, %d NC pins" % (len(PARTS), len(NETS), len(NC)))
    if results[-1][1]:
        sys.exit(1)

    # project-local libraries
    sym_lib = ["kicad_symbol_lib", ["version", "20220914"], ["generator", "kicad_symbol_editor"]]
    sym_lib += [f() for f in LOCAL_SYMBOLS.values()]
    write(os.path.join(KICAD_DIR, "badge.kicad_sym"), sx_dump(sym_lib) + "\n")
    write(os.path.join(KICAD_DIR, "badge.pretty", "ESP32-H2-WROOM-02C.kicad_mod"), build_footprint())
    write(os.path.join(KICAD_DIR, "badge.pretty", "BatteryPads_1x02_P4.0mm.kicad_mod"), build_battery_footprint())
    write(os.path.join(KICAD_DIR, "sym-lib-table"),
          '(sym_lib_table\n  (lib (name "badge")(type "KiCad")(uri "${KIPRJMOD}/badge.kicad_sym")(options "")'
          '(descr "Activity badge: project symbols"))\n)\n')
    write(os.path.join(KICAD_DIR, "fp-lib-table"),
          '(fp_lib_table\n  (lib (name "badge")(type "KiCad")(uri "${KIPRJMOD}/badge.pretty")(options "")'
          '(descr "Activity badge: project footprints"))\n)\n')

    sheet = build_sheet()
    sch_path = os.path.join(KICAD_DIR, PROJECT + ".kicad_sch")
    write(sch_path, sheet.render())
    write(os.path.join(KICAD_DIR, PROJECT + ".kicad_pro"), json.dumps(project_json(sheet.root), indent=2) + "\n")

    os.makedirs(OUT_DIR, exist_ok=True)
    lines, parts = write_bom(os.path.join(OUT_DIR, "bom.csv"))
    report("BOM written", [], "%d lines, %d parts" % (lines, parts))
    report("footprints exist and cover all symbol pins", check_footprints(), "%d parts checked" % len(PARTS))

    cli = shutil.which("kicad-cli")
    if not cli:
        print("SKIP netlist export / comparison and PDF plot: kicad-cli not found")
    else:
        net_path = os.path.join(OUT_DIR, PROJECT + ".net")
        run([cli, "sch", "export", "netlist", "--output", net_path, sch_path])
        errors, count = check_netlist(net_path, sheet)
        report("exported netlist matches the intended netlist", errors,
               "%d nets incl. %d no-connects, %d components" % (count, len(NC), len(PARTS)))
        run([cli, "sch", "export", "pdf", "--output", os.path.join(OUT_DIR, PROJECT + ".pdf"), sch_path])
        report("schematic PDF plotted", [])

    failed = [name for name, errors in results if errors]
    print("RESULT: %s" % ("FAIL (%d check(s))" % len(failed) if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
