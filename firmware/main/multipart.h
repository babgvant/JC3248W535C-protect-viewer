#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#define JPEG_LIMIT 196608
typedef void (*mp_frame_fn)(void *, const uint8_t *, size_t);
typedef struct {
    char line[256];
    char boundary[72];
    size_t line_len, length, used;
    int state;
    bool has_length, jpeg;
    uint8_t *buffer;
    mp_frame_fn emit;
    void *ctx;
    unsigned rejected;
} multipart_t;
bool mp_init(multipart_t *, const char *content_type, uint8_t *, mp_frame_fn, void *);
void mp_feed(multipart_t *, const uint8_t *, size_t);
bool jpeg_profile_supported(const uint8_t *, size_t);
