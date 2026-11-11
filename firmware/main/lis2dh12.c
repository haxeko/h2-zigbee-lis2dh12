#include "lis2dh12.h"

#include "esp_check.h"
#include "esp_log.h"

#include "board.h"

static const char *TAG = "lis2dh12";

/* Register map (LIS2DH12 datasheet, section 8) */
#define REG_WHO_AM_I      0x0F
#define REG_CTRL0         0x1E
#define REG_TEMP_CFG      0x1F
#define REG_CTRL1         0x20
#define REG_CTRL2         0x21
#define REG_CTRL3         0x22
#define REG_CTRL4         0x23
#define REG_CTRL5         0x24
#define REG_CTRL6         0x25
#define REG_OUT_X_L       0x28
#define REG_FIFO_CTRL     0x2E
#define REG_FIFO_SRC      0x2F
#define REG_INT1_CFG      0x30
#define REG_INT2_CFG      0x34
#define REG_CLICK_CFG     0x38

#define REG_AUTO_INCREMENT 0x80 /* MSB of the sub-address: multi-byte access */

#define WHO_AM_I_VALUE    0x33

#define CTRL0_SDO_PU_DISC 0x90 /* disconnect the SA0 pull-up (bits 6:0 must stay 0010000b) */
#define CTRL1_ODR_1HZ     0x10
#define CTRL1_LPEN        0x08 /* low-power mode: 8-bit output */
#define CTRL1_XYZ_EN      0x07
#define CTRL3_I1_WTM      0x04 /* FIFO watermark on INT1 */
#define CTRL4_BDU         0x80
#define CTRL4_FS_2G       0x00 /* HR bit (0x08) stays 0 -> together with LPen = low-power mode */
#define CTRL5_FIFO_EN     0x40
#define FIFO_MODE_BYPASS  0x00
#define FIFO_MODE_STREAM  0x80 /* oldest sample is overwritten when full; INT trigger = INT1 */
#define FIFO_MODE_MASK    0xC0
#define FIFO_FTH_MASK     0x1F
#define FIFO_SRC_OVRN     0x40
#define FIFO_SRC_FSS_MASK 0x1F

/* Low-power mode at ±2 g: 8-bit left-justified data, 16 mg per digit. */
#define LP_2G_MG_PER_DIGIT 16

#define CTRL1_RUN   (CTRL1_ODR_1HZ | CTRL1_LPEN | CTRL1_XYZ_EN)
#define CTRL4_RUN   (CTRL4_BDU | CTRL4_FS_2G)

#define I2C_TIMEOUT_MS 50

static esp_err_t write_reg(lis2dh12_t *acc, uint8_t reg, uint8_t value)
{
    const uint8_t buf[2] = {reg, value};
    return i2c_master_transmit(acc->dev, buf, sizeof(buf), I2C_TIMEOUT_MS);
}

static esp_err_t read_regs(lis2dh12_t *acc, uint8_t reg, uint8_t *data, size_t len)
{
    const uint8_t addr = (len > 1) ? (reg | REG_AUTO_INCREMENT) : reg;
    return i2c_master_transmit_receive(acc->dev, &addr, 1, data, len, I2C_TIMEOUT_MS);
}

/* WTM flag (and INT1) asserts when the number of unread samples exceeds FTH. */
static uint8_t watermark_to_fth(uint8_t watermark)
{
    if (watermark < 1) {
        watermark = 1;
    } else if (watermark > LIS2DH12_FIFO_DEPTH - 1) {
        watermark = LIS2DH12_FIFO_DEPTH - 1;
    }
    return (uint8_t)(watermark - 1);
}

esp_err_t lis2dh12_open(lis2dh12_t *acc)
{
    const i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = BOARD_I2C_SDA_GPIO,
        .scl_io_num = BOARD_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        /* The board has external pull-ups; the weak internal ones only help on a bare devkit. */
        .flags.enable_internal_pullup = true,
    };
    const i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = BOARD_LIS2DH12_ADDR,
        .scl_speed_hz = BOARD_I2C_FREQ_HZ,
    };

    acc->bus = NULL;
    acc->dev = NULL;
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &acc->bus), TAG, "I2C bus init failed");
    esp_err_t err = i2c_master_bus_add_device(acc->bus, &dev_cfg, &acc->dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2C device init failed: %s", esp_err_to_name(err));
        lis2dh12_close(acc);
    }
    return err;
}

void lis2dh12_close(lis2dh12_t *acc)
{
    if (acc->dev) {
        i2c_master_bus_rm_device(acc->dev);
        acc->dev = NULL;
    }
    if (acc->bus) {
        i2c_del_master_bus(acc->bus);
        acc->bus = NULL;
    }
}

esp_err_t lis2dh12_is_configured(lis2dh12_t *acc, bool *configured)
{
    uint8_t ctrl[5]; /* CTRL_REG1..CTRL_REG5 */
    uint8_t fifo_ctrl;

    *configured = false;
    ESP_RETURN_ON_ERROR(read_regs(acc, REG_CTRL1, ctrl, sizeof(ctrl)), TAG, "read CTRL failed");
    ESP_RETURN_ON_ERROR(read_regs(acc, REG_FIFO_CTRL, &fifo_ctrl, 1), TAG, "read FIFO_CTRL failed");

    *configured = ctrl[0] == CTRL1_RUN && ctrl[2] == CTRL3_I1_WTM && ctrl[3] == CTRL4_RUN &&
                  (ctrl[4] & CTRL5_FIFO_EN) && (fifo_ctrl & FIFO_MODE_MASK) == FIFO_MODE_STREAM;
    return ESP_OK;
}

esp_err_t lis2dh12_configure(lis2dh12_t *acc, uint8_t watermark)
{
    uint8_t who = 0;
    ESP_RETURN_ON_ERROR(read_regs(acc, REG_WHO_AM_I, &who, 1), TAG, "sensor not responding");
    ESP_RETURN_ON_FALSE(who == WHO_AM_I_VALUE, ESP_ERR_NOT_FOUND, TAG, "unexpected WHO_AM_I 0x%02x", who);

    /*
     * The sensor stays powered across MCU resets, so write every register we
     * depend on instead of assuming power-on defaults. Sampling starts last.
     */
    const struct {
        uint8_t reg;
        uint8_t value;
    } seq[] = {
        {REG_CTRL1, 0x00},              /* power-down while reconfiguring */
        {REG_CTRL0, CTRL0_SDO_PU_DISC}, /* no current through the SA0 pull-up */
        {REG_TEMP_CFG, 0x00},           /* temperature sensor off */
        {REG_CTRL2, 0x00},              /* no high-pass filter: a_mag needs gravity */
        {REG_CTRL3, CTRL3_I1_WTM},
        {REG_CTRL4, CTRL4_RUN},
        {REG_CTRL5, CTRL5_FIFO_EN},
        {REG_CTRL6, 0x00}, /* nothing on INT2, interrupts active high */
        {REG_INT1_CFG, 0x00},
        {REG_INT2_CFG, 0x00},
        {REG_CLICK_CFG, 0x00},
        {REG_FIFO_CTRL, FIFO_MODE_BYPASS}, /* flush the FIFO */
        {REG_FIFO_CTRL, FIFO_MODE_STREAM | watermark_to_fth(watermark)},
        {REG_CTRL1, CTRL1_RUN}, /* 1 Hz, low-power, X/Y/Z on */
    };
    for (size_t i = 0; i < sizeof(seq) / sizeof(seq[0]); i++) {
        ESP_RETURN_ON_ERROR(write_reg(acc, seq[i].reg, seq[i].value), TAG, "write reg 0x%02x failed", seq[i].reg);
    }
    return ESP_OK;
}

esp_err_t lis2dh12_set_watermark(lis2dh12_t *acc, uint8_t watermark)
{
    /* Rewriting FIFO_CTRL with the same mode keeps the FIFO content. */
    return write_reg(acc, REG_FIFO_CTRL, FIFO_MODE_STREAM | watermark_to_fth(watermark));
}

esp_err_t lis2dh12_fifo_read(lis2dh12_t *acc, lis2dh12_sample_t out[LIS2DH12_FIFO_DEPTH], size_t *count, bool *overrun)
{
    uint8_t src = 0;
    uint8_t raw[LIS2DH12_FIFO_DEPTH * 6];

    *count = 0;
    *overrun = false;
    ESP_RETURN_ON_ERROR(read_regs(acc, REG_FIFO_SRC, &src, 1), TAG, "read FIFO_SRC failed");

    /* FSS saturates at 31; a full FIFO (32 samples) is flagged by OVRN. */
    *overrun = (src & FIFO_SRC_OVRN) != 0;
    size_t n = *overrun ? LIS2DH12_FIFO_DEPTH : (src & FIFO_SRC_FSS_MASK);
    if (n == 0) {
        return ESP_OK;
    }

    /*
     * With the FIFO enabled the address pointer wraps from OUT_Z_H back to
     * OUT_X_L, so one burst of n * 6 bytes pops n samples.
     */
    ESP_RETURN_ON_ERROR(read_regs(acc, REG_OUT_X_L, raw, n * 6), TAG, "read FIFO failed");

    for (size_t i = 0; i < n; i++) {
        const uint8_t *s = &raw[i * 6];
        /* Low-power mode: the 8-bit sample is the high byte of each axis. */
        out[i].x_mg = (int16_t)((int8_t)s[1] * LP_2G_MG_PER_DIGIT);
        out[i].y_mg = (int16_t)((int8_t)s[3] * LP_2G_MG_PER_DIGIT);
        out[i].z_mg = (int16_t)((int8_t)s[5] * LP_2G_MG_PER_DIGIT);
    }
    *count = n;
    return ESP_OK;
}
