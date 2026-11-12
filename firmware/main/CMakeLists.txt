idf_component_register(SRCS "main.c" "board.c" "multipart.c" "viewer_state.c" "font.c" "settings.c" "ui.c" "web.c"
    INCLUDE_DIRS "." EMBED_FILES "testcard.jpg"
    REQUIRES esp_wifi esp_http_client esp_http_server esp_event nvs_flash esp_netif esp_timer esp_lcd esp_driver_spi esp_driver_i2c esp_driver_ledc mbedtls espressif__cjson)
