#pragma once

#include <stdint.h>

typedef struct {
    int16_t peak;
    uint32_t rms;
    uint32_t samples;
} audio_capture_stats_t;

audio_capture_stats_t audio_math_compute_stats(const int16_t *samples, uint32_t sample_count);
