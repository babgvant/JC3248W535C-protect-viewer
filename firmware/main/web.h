#pragma once
#include <stdbool.h>
// Capture ESP log output into a RAM ring (still forwarded to the console). Call first thing in app_main.
void web_log_init(void);
// Start the HTTP server: encoder IP/port/key form plus live log. on_gateway_change runs after a save.
typedef struct {char id[33],name[32];bool enabled;} web_cam_t;
typedef struct {
    void (*gateway_changed)(void);
    int (*list_cameras)(web_cam_t *out,int max);          // shown cameras in order, then hidden ones
    void (*apply_cameras)(const char ids[][33],int n);     // ids to show, in order
} web_hooks_t;
void web_start(const web_hooks_t *hooks);
