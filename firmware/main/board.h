/*
 * Board pin map — activity badge, ESP32-H2-WROOM-02C.
 * Keep in sync with hardware/README.md (schematic net names in comments).
 */
#pragma once

#include "driver/gpio.h"
#include "hal/adc_types.h"

/* I2C bus to the LIS2DH12 (external 4.7 kΩ pull-ups to 3V3) */
#define BOARD_I2C_SDA_GPIO      GPIO_NUM_4   /* module pin 3,  net I2C_SDA */
#define BOARD_I2C_SCL_GPIO      GPIO_NUM_5   /* module pin 4,  net I2C_SCL */
#define BOARD_I2C_FREQ_HZ       400000
#define BOARD_LIS2DH12_ADDR     0x19         /* SA0 tied to VDD_IO */

/*
 * LIS2DH12 interrupt lines. Both are on LP GPIOs (GPIO7..14 on ESP32-H2), the
 * only pins that can wake the chip from deep sleep (EXT1).
 */
#define BOARD_ACC_INT1_GPIO     GPIO_NUM_10  /* module pin 5,  net ACC_INT1: FIFO watermark, active high */
#define BOARD_ACC_INT2_GPIO     GPIO_NUM_11  /* module pin 6,  net ACC_INT2: reserved, unused by firmware */

/* User/BOOT button: strapping pin GPIO9, LP GPIO, active low, 10 kΩ pull-up */
#define BOARD_BUTTON_GPIO       GPIO_NUM_9   /* module pin 8,  net BOOT */

/* Status LED, active high through 2.2 kΩ */
#define BOARD_LED_GPIO          GPIO_NUM_22  /* module pin 15, net LED_STATUS */

/*
 * Battery sense: VBAT -> P-FET switch -> 100k/100k divider -> ADC.
 * The divider only draws current while BOARD_VBAT_EN_GPIO is high.
 */
#define BOARD_VBAT_EN_GPIO      GPIO_NUM_0    /* module pin 28, net VBAT_SENSE_EN */
#define BOARD_VBAT_ADC_CHANNEL  ADC_CHANNEL_0 /* GPIO1, module pin 27, net VBAT_SENSE */
#define BOARD_VBAT_DIVIDER      2             /* V(bat) = V(adc) * 2 */
#define BOARD_VBAT_SETTLE_MS    5             /* RC of the divider node: 50 kΩ * 10 nF = 0.5 ms */
