#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef struct { unsigned selected, count; uint32_t generation; bool off; } viewer_state_t;
bool viewer_switch(viewer_state_t *, int direction);
bool viewer_accept(const viewer_state_t *, uint32_t generation);
int viewer_gesture(int dx, int dy, unsigned duration_ms);
