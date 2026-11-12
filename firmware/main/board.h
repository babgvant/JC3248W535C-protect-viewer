#pragma once
#include <stdbool.h>
#include <stdint.h>
// Provisional JC3248W535EN/C N16R8 map; verify PCB revision before flashing.
#define LCD_CS 45
#define LCD_CLK 47
#define LCD_D0 21
#define LCD_D1 48
#define LCD_D2 40
#define LCD_D3 39
#define LCD_BL 1
#define TOUCH_SDA 4
#define TOUCH_SCL 8
void board_init(void);
bool board_touch(int *x,int *y,bool portrait);
void board_brightness(unsigned percent);
bool board_present(const uint16_t *native_rgb565, bool (*current)(void *),void *ctx);
