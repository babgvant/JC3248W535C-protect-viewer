#pragma once
#include <stdint.h>
#include <stdbool.h>
void overlay_set_portrait(bool portrait);
void overlay_text(uint16_t *canvas,int x,int y,const char *text,uint16_t color);
void overlay_rect(uint16_t *canvas,int x,int y,int w,int h,uint16_t color);
