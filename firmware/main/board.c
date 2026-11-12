#include "board.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "driver/spi_master.h"
#include "driver/i2c_master.h"
#include "driver/ledc.h"
#include "esp_lcd_axs15231b.h"
#include "esp_lcd_panel_ops.h"
#include "esp_heap_caps.h"

static esp_lcd_panel_handle_t panel;
static esp_lcd_touch_handle_t touch;
static SemaphoreHandle_t done;
static uint16_t *dma;
static bool transferred(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *e,void *ctx) {
    BaseType_t wake=pdFALSE; xSemaphoreGiveFromISR(done,&wake); return wake==pdTRUE;
}
void board_init(void) {
    done=xSemaphoreCreateBinary(); assert(done);
    dma=heap_caps_aligned_alloc(64,320*16*2,MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL); assert(dma);
    spi_bus_config_t bus=AXS15231B_PANEL_BUS_QSPI_CONFIG(LCD_CLK,LCD_D0,LCD_D1,LCD_D2,LCD_D3,320*16*2);
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST,&bus,SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_handle_t io;
    esp_lcd_panel_io_spi_config_t cfg=AXS15231B_PANEL_IO_QSPI_CONFIG(LCD_CS,transferred,NULL);
    cfg.trans_queue_depth=1;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,&cfg,&io));
    axs15231b_vendor_config_t vendor={.flags.use_qspi_interface=1};
    esp_lcd_panel_dev_config_t pc={.reset_gpio_num=-1,.rgb_ele_order=LCD_RGB_ELEMENT_ORDER_RGB,.bits_per_pixel=16,.vendor_config=&vendor};
    ESP_ERROR_CHECK(esp_lcd_new_panel_axs15231b(io,&pc,&panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel)); ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel,true));
    ledc_timer_config_t timer={.speed_mode=LEDC_LOW_SPEED_MODE,.duty_resolution=LEDC_TIMER_10_BIT,.timer_num=LEDC_TIMER_0,.freq_hz=5000,.clk_cfg=LEDC_AUTO_CLK};
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    ledc_channel_config_t channel={.gpio_num=LCD_BL,.speed_mode=LEDC_LOW_SPEED_MODE,.channel=LEDC_CHANNEL_0,.timer_sel=LEDC_TIMER_0,.duty=700};
    ESP_ERROR_CHECK(ledc_channel_config(&channel));
    i2c_master_bus_handle_t ib;
    i2c_master_bus_config_t bc={.i2c_port=I2C_NUM_0,.sda_io_num=TOUCH_SDA,.scl_io_num=TOUCH_SCL,.clk_source=I2C_CLK_SRC_DEFAULT,.flags.enable_internal_pullup=1};
    ESP_ERROR_CHECK(i2c_new_master_bus(&bc,&ib));
    esp_lcd_panel_io_i2c_config_t ic=ESP_LCD_TOUCH_IO_I2C_AXS15231B_CONFIG_EX(400000);
    esp_lcd_panel_io_handle_t ti;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c(ib,&ic,&ti));
    esp_lcd_touch_config_t tc={.x_max=320,.y_max=480,.rst_gpio_num=-1,.int_gpio_num=-1};
    ESP_ERROR_CHECK(esp_lcd_touch_new_i2c_axs15231b(ti,&tc,&touch));
}
bool board_touch(int *x,int *y) {
    if(esp_lcd_touch_read_data(touch)!=ESP_OK) return false;
    esp_lcd_touch_point_data_t point; uint8_t count=0;
    if(esp_lcd_touch_get_data(touch,&point,&count,1)!=ESP_OK || !count) return false;
    *x=479-point.y; *y=point.x; return true;
}
void board_brightness(unsigned percent) {
    static unsigned previous=101;
    if(percent>100) percent=100;
    if(percent==previous) return;
    previous=percent;
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_0,percent*1023/100));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_0));
}
bool board_present(const uint16_t *pixels,bool (*current)(void *),void *ctx) {
    // Single render task owns this buffer and every LCD command. DMA never sees PSRAM.
    for(int y=0;y<480;y+=16) {
        if(current && !current(ctx)) return false;
        memcpy(dma,pixels+y*320,320*16*2);
        ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel,0,y,320,y+16,dma));
        // Buffer remains immutable until completion. A timeout deliberately restarts.
        if(xSemaphoreTake(done,pdMS_TO_TICKS(500))!=pdTRUE) abort();
    }
    return true;
}
