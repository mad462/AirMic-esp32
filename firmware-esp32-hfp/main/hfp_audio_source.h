#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define HFP_AUDIO_I2S_SAMPLE_RATE_HZ 16000
#define HFP_AUDIO_CVSD_SAMPLE_RATE_HZ 8000
#define HFP_AUDIO_BITS_PER_SAMPLE 16

typedef void (*hfp_audio_source_ready_cb_t)(void);

typedef struct {
    uint16_t mic_gain_q8;
    uint16_t noise_gate;
    uint16_t tone_gain_q8;
    uint8_t sample_shift;
} hfp_audio_source_config_t;

esp_err_t hfp_audio_source_init(void);
esp_err_t hfp_audio_source_start(void);
esp_err_t hfp_audio_source_stop(void);
void hfp_audio_source_set_ptt(bool pressed);
bool hfp_audio_source_is_running(void);
void hfp_audio_source_inject_tone_start(void);
void hfp_audio_source_inject_tone_a(void);
void hfp_audio_source_inject_tone_b(void);
void hfp_audio_source_inject_tone_c(void);
void hfp_audio_source_get_config(hfp_audio_source_config_t *config);
esp_err_t hfp_audio_source_set_config(const hfp_audio_source_config_t *config);
esp_err_t hfp_audio_source_save_config(void);
esp_err_t hfp_audio_source_reset_config(void);
esp_err_t hfp_audio_source_set_sr_enabled(bool enabled);
bool hfp_audio_source_is_sr_enabled(void);
bool hfp_audio_source_is_sr_initialized(void);

uint32_t hfp_audio_source_read(uint8_t *dst, uint32_t len);
void hfp_audio_source_set_ready_callback(hfp_audio_source_ready_cb_t callback);
void hfp_audio_source_notify_ready(void);
