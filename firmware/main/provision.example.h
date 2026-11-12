#pragma once
// Copy to provision.h (ignored). Never commit real values. These enter the binary.
#define VIEWER_SSID "YOUR_WIFI"
#define VIEWER_PASSWORD "YOUR_PASSWORD"
#define VIEWER_GATEWAY "http://192.168.1.50:8080"
#define VIEWER_TOKEN "REPLACE_WITH_64_HEX_CHARACTERS_FROM_PI"
// HTTPS uses the IDF certificate bundle, or this PEM for a private LAN CA.
#define VIEWER_CA_PEM NULL
// A reachable LOCAL NTP server is required for HTTPS certificate date checks.
#define VIEWER_NTP_SERVER "192.168.1.1"
// 0 = always on. Video stops while the screen is off.
#define VIEWER_SCREEN_OFF_SECONDS 0
#define VIEWER_DIM_SECONDS 0
// Diagnostic mode: display a generated RGB chart without requiring Wi-Fi.
#define VIEWER_BRINGUP_ONLY 0
