idf_component_register(SRCS "main.c" "board.c" "multipart.c" "viewer_state.c" "font.c"
    INCLUDE_DIRS "." EMBED_FILES "testcard.jpg"
    REQUIRES esp_wifi esp_http_client esp_event nvs_flash esp_netif esp_timer esp_lcd esp_driver_spi esp_driver_i2c esp_driver_ledc mbedtls json)
