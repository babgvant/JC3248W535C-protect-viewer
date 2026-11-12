#pragma once
#include <stdint.h>
#include <stdbool.h>
void overlay_set_portrait(bool portrait);
void overlay_text(uint16_t *canvas,int x,int y,const char *text,uint16_t color);
// Lowercase letters use lower_color so case is visible in the 3x5 uppercase-only font.
void overlay_text_cased(uint16_t *canvas,int x,int y,const char *text,uint16_t upper_color,uint16_t lower_color);
void overlay_rect(uint16_t *canvas,int x,int y,int w,int h,uint16_t color);
