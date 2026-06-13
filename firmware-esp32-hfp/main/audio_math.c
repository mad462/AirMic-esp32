#include "audio_math.h"

#include <limits.h>
#include <math.h>
#include <stddef.h>

static int16_t i32_abs_to_i16_sat(int32_t value)
{
    if (value == INT32_MIN) {
        return INT16_MAX;
    }

    int32_t abs_value = value < 0 ? -value : value;
    if (abs_value > INT16_MAX) {
        return INT16_MAX;
    }
    return (int16_t)abs_value;
}

audio_capture_stats_t audio_math_compute_stats(const int16_t *samples, uint32_t sample_count)
{
    audio_capture_stats_t stats = {
        .peak = 0,
        .rms = 0,
        .samples = sample_count,
    };

    if (samples == NULL || sample_count == 0) {
        stats.samples = 0;
        return stats;
    }

    uint64_t sum_squares = 0;
    int16_t peak = 0;

    for (uint32_t i = 0; i < sample_count; ++i) {
        int16_t abs_sample = i32_abs_to_i16_sat(samples[i]);
        if (abs_sample > peak) {
            peak = abs_sample;
        }
        int32_t s = samples[i];
        sum_squares += (uint64_t)(s * s);
    }

    stats.peak = peak;
    stats.rms = (uint32_t)sqrt((double)sum_squares / (double)sample_count);
    return stats;
}
