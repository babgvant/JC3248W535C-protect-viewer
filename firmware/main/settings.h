#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
// Runtime configuration. Defaults come from provision.h; values saved from the device UI or web page override them (NVS).
typedef struct {char ssid[33],pass[65],host[64],token[129]; uint16_t port; bool https;} settings_t;
void settings_init(void); // call after nvs_flash_init()
void settings_get(settings_t *out);
bool settings_set_wifi(const char *ssid,const char *pass);
// An empty or NULL token keeps the stored one.
bool settings_set_gateway(const char *host,uint16_t port,const char *token);
// Visible camera ids in display order, comma separated. Empty = show every camera in gateway order.
void settings_get_cams(char *out,size_t cap);
bool settings_set_cams(const char *csv);
bool settings_valid_host(const char *host);
bool settings_valid_token(const char *token);
