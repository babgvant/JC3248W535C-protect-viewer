#pragma once
#include <stdbool.h>
#include <stdint.h>
// On-device settings pages (Wi-Fi scan / join). Always drawn landscape 480x320.
void ui_init(void (*wifi_changed)(void));
bool ui_active(void);
uint32_t ui_version(void);
void ui_open(void);
void ui_tap(int x,int y);
void ui_draw(uint16_t *canvas);
// Gear icon for the video overlay (top-right); hit test with ui_gear_hit.
void ui_draw_gear(uint16_t *canvas,int logical_width);
bool ui_gear_hit(int x,int y,int logical_width);
