#include "hfp_audio_source.h"

#include <inttypes.h>
#include <limits.h>
#include <math.h>
#include <string.h>

#include "audio_math.h"
#include "board_config.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"
#include "freertos/task.h"
#include "nvs.h"

static const char *TAG = "hfp_audio";

#define HFP_AUDIO_TASK_STACK       10240
#define HFP_AUDIO_TASK_PRIORITY    5
#define HFP_AUDIO_RAW_SAMPLES      512
#define HFP_AUDIO_RINGBUF_BYTES    8192
#define HFP_AUDIO_PRINT_INTERVAL_MS 1000
#define TONE_SYMBOL_SAMPLES        1200
#define TONE_GAP_SAMPLES           160
#define TONE_TOTAL_SAMPLES         (TONE_SYMBOL_SAMPLES + TONE_GAP_SAMPLES)
#define TONE_AMPLITUDE             10000.0f
#define TONE_MIC_DUCK_Q8           0
#define TONE_EVENT_QUEUE_LEN       8
#define TONE_PI                    3.14159265358979323846f
#define CFG_NVS_NAMESPACE          "airmic_audio"
#define CFG_NVS_KEY_GAIN_Q8        "gain_q8"
#define CFG_NVS_KEY_GATE           "gate"
#define CFG_NVS_KEY_TONE_Q8        "tone_q8"
#define CFG_NVS_KEY_SHIFT          "shift"
#define CFG_DEFAULT_MIC_GAIN_Q8    1024
#define CFG_DEFAULT_NOISE_GATE     0
#define CFG_DEFAULT_TONE_GAIN_Q8   256
#define CFG_DEFAULT_SAMPLE_SHIFT   11
#define CFG_MIN_MIC_GAIN_Q8        64
#define CFG_MAX_MIC_GAIN_Q8        4096
#define CFG_MAX_NOISE_GATE         6000
#define CFG_MIN_TONE_GAIN_Q8       64
#define CFG_MAX_TONE_GAIN_Q8       768
#define CFG_MIN_SAMPLE_SHIFT       8
#define CFG_MAX_SAMPLE_SHIFT       18
#define HPF_ALPHA_Q15              32276
#define NOISE_GATE_HYSTERESIS_DIV  4

typedef enum {
    TONE_EVENT_NONE = 0,
    TONE_EVENT_START,
    TONE_EVENT_STOP,
    TONE_EVENT_A,
    TONE_EVENT_B,
    TONE_EVENT_C,
} tone_event_t;

static i2s_chan_handle_t s_rx_chan;
static RingbufHandle_t s_pcm_ringbuf;
static TaskHandle_t s_audio_task;
static volatile bool s_running;
static volatile bool s_ptt_pressed;
static bool s_initialized;
static hfp_audio_source_ready_cb_t s_ready_cb;
static volatile bool s_filter_reset_requested;
static portMUX_TYPE s_tone_lock = portMUX_INITIALIZER_UNLOCKED;
static tone_event_t s_tone_queue[TONE_EVENT_QUEUE_LEN];
static uint8_t s_tone_queue_head;
static uint8_t s_tone_queue_tail;
static tone_event_t s_active_tone;
static uint32_t s_active_tone_sample;
static hfp_audio_source_config_t s_config = {
    .mic_gain_q8 = CFG_DEFAULT_MIC_GAIN_Q8,
    .noise_gate = CFG_DEFAULT_NOISE_GATE,
    .tone_gain_q8 = CFG_DEFAULT_TONE_GAIN_Q8,
    .sample_shift = CFG_DEFAULT_SAMPLE_SHIFT,
};
static portMUX_TYPE s_config_lock = portMUX_INITIALIZER_UNLOCKED;
static int32_t s_raw_samples[HFP_AUDIO_RAW_SAMPLES];
static int16_t s_pcm16_16k[HFP_AUDIO_RAW_SAMPLES];
static int16_t s_pcm16_8k[HFP_AUDIO_RAW_SAMPLES / 2];

static int16_t clamp_i16(int32_t value)
{
    if (value > INT16_MAX) {
        return INT16_MAX;
    }
    if (value < INT16_MIN) {
        return INT16_MIN;
    }
    return (int16_t)value;
}

static uint16_t clamp_u16(uint32_t value, uint16_t min_value, uint16_t max_value)
{
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return (uint16_t)value;
}

static hfp_audio_source_config_t current_config(void)
{
    hfp_audio_source_config_t config;
    portENTER_CRITICAL(&s_config_lock);
    config = s_config;
    portEXIT_CRITICAL(&s_config_lock);
    return config;
}

static void log_config(const char *prefix, const hfp_audio_source_config_t *config)
{
    ESP_LOGI(TAG, "%s gain_q8=%u gain=%.2fx noise_gate=%u tone_q8=%u tone=%.2fx shift=%u",
             prefix,
             config->mic_gain_q8,
             (double)config->mic_gain_q8 / 256.0,
             config->noise_gate,
             config->tone_gain_q8,
             (double)config->tone_gain_q8 / 256.0,
             config->sample_shift);
}

static esp_err_t load_config_from_nvs(void)
{
    nvs_handle_t nvs;
    esp_err_t ret = nvs_open(CFG_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (ret == ESP_ERR_NVS_NOT_FOUND) {
        log_config("audio config default", &s_config);
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(ret, TAG, "open audio config nvs failed");

    uint16_t gain_q8 = s_config.mic_gain_q8;
    uint16_t noise_gate = s_config.noise_gate;
    uint16_t tone_q8 = s_config.tone_gain_q8;
    uint8_t sample_shift = s_config.sample_shift;
    (void)nvs_get_u16(nvs, CFG_NVS_KEY_GAIN_Q8, &gain_q8);
    (void)nvs_get_u16(nvs, CFG_NVS_KEY_GATE, &noise_gate);
    (void)nvs_get_u16(nvs, CFG_NVS_KEY_TONE_Q8, &tone_q8);
    (void)nvs_get_u8(nvs, CFG_NVS_KEY_SHIFT, &sample_shift);
    nvs_close(nvs);

    hfp_audio_source_config_t loaded = {
        .mic_gain_q8 = clamp_u16(gain_q8, CFG_MIN_MIC_GAIN_Q8, CFG_MAX_MIC_GAIN_Q8),
        .noise_gate = clamp_u16(noise_gate, 0, CFG_MAX_NOISE_GATE),
        .tone_gain_q8 = clamp_u16(tone_q8, CFG_MIN_TONE_GAIN_Q8, CFG_MAX_TONE_GAIN_Q8),
        .sample_shift = (uint8_t)clamp_u16(sample_shift, CFG_MIN_SAMPLE_SHIFT, CFG_MAX_SAMPLE_SHIFT),
    };
    ESP_RETURN_ON_ERROR(hfp_audio_source_set_config(&loaded), TAG, "apply loaded audio config failed");
    log_config("audio config loaded", &loaded);
    return ESP_OK;
}

static int16_t convert_raw_sample(int32_t raw_sample, const hfp_audio_source_config_t *config)
{
    return (int16_t)(raw_sample >> config->sample_shift);
}

typedef struct {
    int32_t prev_input;
    int32_t prev_output;
} dc_block_state_t;

typedef struct {
    int16_t prev_sample;
    bool has_prev_sample;
} decimator_state_t;

typedef struct {
    bool gate_open;
    uint16_t gain_q15;
} gate_state_t;

static void reset_audio_filter_states(dc_block_state_t *dc_state,
                                      gate_state_t *gate_state,
                                      decimator_state_t *decimator_state)
{
    if (dc_state != NULL) {
        dc_state->prev_input = 0;
        dc_state->prev_output = 0;
    }
    if (gate_state != NULL) {
        gate_state->gate_open = false;
        gate_state->gain_q15 = 32767;
    }
    if (decimator_state != NULL) {
        decimator_state->prev_sample = 0;
        decimator_state->has_prev_sample = false;
    }
}

static int16_t apply_dc_block(int16_t sample, dc_block_state_t *state)
{
    int32_t x = sample;
    int32_t y = x - state->prev_input + ((state->prev_output * HPF_ALPHA_Q15) >> 15);
    state->prev_input = x;
    state->prev_output = y;
    return clamp_i16(y);
}

static int16_t apply_noise_gate_smoothing(int16_t sample,
                                          const hfp_audio_source_config_t *config,
                                          gate_state_t *state)
{
    if (config->noise_gate == 0) {
        state->gate_open = true;
        state->gain_q15 = 32767;
        return sample;
    }

    int32_t abs_sample = sample < 0 ? -(int32_t)sample : (int32_t)sample;
    uint16_t open_threshold = config->noise_gate;
    uint16_t close_threshold = open_threshold > NOISE_GATE_HYSTERESIS_DIV
        ? (uint16_t)(open_threshold - (open_threshold / NOISE_GATE_HYSTERESIS_DIV))
        : 0;

    if (!state->gate_open && abs_sample >= open_threshold) {
        state->gate_open = true;
    } else if (state->gate_open && abs_sample <= close_threshold) {
        state->gate_open = false;
    }

    uint16_t target_gain_q15 = state->gate_open ? 32767 : 0;
    uint16_t step_q15 = state->gate_open ? 4096 : 1536;
    if (state->gain_q15 < target_gain_q15) {
        uint32_t next_gain = (uint32_t)state->gain_q15 + step_q15;
        state->gain_q15 = (uint16_t)(next_gain > target_gain_q15 ? target_gain_q15 : next_gain);
    } else if (state->gain_q15 > target_gain_q15) {
        uint32_t next_gain = state->gain_q15 > step_q15 ? (uint32_t)state->gain_q15 - step_q15 : 0;
        state->gain_q15 = (uint16_t)(next_gain < target_gain_q15 ? target_gain_q15 : next_gain);
    }

    int32_t scaled = ((int32_t)sample * (int32_t)state->gain_q15) >> 15;
    return clamp_i16(scaled);
}

static int16_t apply_mic_gain(int16_t sample, const hfp_audio_source_config_t *config)
{
    int32_t scaled = ((int32_t)sample * (int32_t)config->mic_gain_q8) / 256;
    return clamp_i16(scaled);
}

static bool decimate_to_8k(int16_t sample, decimator_state_t *state, int16_t *out_sample)
{
    if (!state->has_prev_sample) {
        state->prev_sample = sample;
        state->has_prev_sample = true;
        return false;
    }

    int32_t filtered = (int32_t)state->prev_sample + ((int32_t)sample * 3);
    *out_sample = clamp_i16(filtered / 4);
    state->prev_sample = sample;
    state->has_prev_sample = false;
    return true;
}

static const char *tone_event_name(tone_event_t event)
{
    switch (event) {
    case TONE_EVENT_START:
        return "START";
    case TONE_EVENT_STOP:
        return "STOP";
    case TONE_EVENT_A:
        return "A";
    case TONE_EVENT_B:
        return "B";
    case TONE_EVENT_C:
        return "C";
    default:
        return "NONE";
    }
}

static bool tone_event_pop(tone_event_t *event)
{
    bool has_event = false;

    portENTER_CRITICAL(&s_tone_lock);
    if (s_tone_queue_head != s_tone_queue_tail) {
        *event = s_tone_queue[s_tone_queue_tail];
        s_tone_queue_tail = (uint8_t)((s_tone_queue_tail + 1) % TONE_EVENT_QUEUE_LEN);
        has_event = true;
    }
    portEXIT_CRITICAL(&s_tone_lock);

    return has_event;
}

static void tone_event_push(tone_event_t event)
{
    bool queued = false;

    portENTER_CRITICAL(&s_tone_lock);
    uint8_t next_head = (uint8_t)((s_tone_queue_head + 1) % TONE_EVENT_QUEUE_LEN);
    if (next_head != s_tone_queue_tail) {
        s_tone_queue[s_tone_queue_head] = event;
        s_tone_queue_head = next_head;
        queued = true;
    }
    portEXIT_CRITICAL(&s_tone_lock);

    if (queued) {
        hfp_audio_source_notify_ready();
        ESP_LOGI(TAG, "queued HFP tone %s", tone_event_name(event));
    } else {
        ESP_LOGW(TAG, "tone queue full, dropped %s", tone_event_name(event));
    }
}

static float tone_sample_value(tone_event_t event, uint32_t sample_index, uint16_t tone_gain_q8)
{
    if (event == TONE_EVENT_NONE || sample_index >= TONE_SYMBOL_SAMPLES) {
        return 0.0f;
    }

    float low_freq = 1200.0f;
    float high_freq = 2750.0f;
    switch (event) {
    case TONE_EVENT_A:
        low_freq = 1200.0f;
        high_freq = 2300.0f;
        break;
    case TONE_EVENT_B:
        low_freq = 1450.0f;
        high_freq = 2300.0f;
        break;
    case TONE_EVENT_C:
        low_freq = 1450.0f;
        high_freq = 2750.0f;
        break;
    case TONE_EVENT_STOP:
        low_freq = 1750.0f;
        high_freq = 3150.0f;
        break;
    case TONE_EVENT_START:
    default:
        low_freq = 1200.0f;
        high_freq = 2750.0f;
        break;
    }

    float t = (float)sample_index / (float)HFP_AUDIO_CVSD_SAMPLE_RATE_HZ;
    float envelope = sinf(TONE_PI * (float)sample_index / (float)(TONE_SYMBOL_SAMPLES - 1));
    float low = sinf(2.0f * TONE_PI * low_freq * t);
    float high = sinf(2.0f * TONE_PI * high_freq * t);
    return (low + high) * 0.5f * TONE_AMPLITUDE * ((float)tone_gain_q8 / 256.0f) * envelope;
}

static void mix_pending_tones(uint8_t *dst, uint32_t len)
{
    int16_t *samples = (int16_t *)dst;
    uint32_t sample_count = len / sizeof(int16_t);

    for (uint32_t i = 0; i < sample_count; ++i) {
        if (s_active_tone == TONE_EVENT_NONE) {
            tone_event_t next_event = TONE_EVENT_NONE;
            if (!tone_event_pop(&next_event)) {
                continue;
            }
            s_active_tone = next_event;
            s_active_tone_sample = 0;
            ESP_LOGI(TAG, "injecting HFP tone %s", tone_event_name(s_active_tone));
        }

        hfp_audio_source_config_t config = current_config();
        float tone = tone_sample_value(s_active_tone, s_active_tone_sample, config.tone_gain_q8);
        int32_t ducked = ((int32_t)samples[i] * TONE_MIC_DUCK_Q8) / 256;
        samples[i] = clamp_i16(ducked + (int32_t)tone);

        s_active_tone_sample++;
        if (s_active_tone_sample >= TONE_TOTAL_SAMPLES) {
            ESP_LOGI(TAG, "finished HFP tone %s", tone_event_name(s_active_tone));
            s_active_tone = TONE_EVENT_NONE;
            s_active_tone_sample = 0;
        }
    }
}

static void ringbuf_clear(void)
{
    if (s_pcm_ringbuf == NULL) {
        return;
    }

    while (true) {
        size_t item_size = 0;
        uint8_t *item = xRingbufferReceiveUpTo(s_pcm_ringbuf, &item_size, 0, HFP_AUDIO_RINGBUF_BYTES);
        if (item == NULL) {
            break;
        }
        vRingbufferReturnItem(s_pcm_ringbuf, item);
    }
}

static void audio_task(void *arg)
{
    (void)arg;

    TickType_t last_print = xTaskGetTickCount();
    dc_block_state_t dc_state = {0};
    gate_state_t gate_state = {
        .gate_open = false,
        .gain_q15 = 32767,
    };
    decimator_state_t decimator_state = {0};

    while (true) {
        if (!s_running) {
            reset_audio_filter_states(&dc_state, &gate_state, &decimator_state);
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(
            s_rx_chan,
            s_raw_samples,
            sizeof(s_raw_samples),
            &bytes_read,
            pdMS_TO_TICKS(100));

        if (ret == ESP_ERR_TIMEOUT) {
            continue;
        }
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "I2S read failed: %s", esp_err_to_name(ret));
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        uint32_t sample_count = bytes_read / sizeof(s_raw_samples[0]);
        if (sample_count < 2) {
            continue;
        }

        if (!s_ptt_pressed) {
            ringbuf_clear();
            reset_audio_filter_states(&dc_state, &gate_state, &decimator_state);
            continue;
        }

        if (s_filter_reset_requested) {
            reset_audio_filter_states(&dc_state, &gate_state, &decimator_state);
            s_filter_reset_requested = false;
        }

        hfp_audio_source_config_t config = current_config();
        for (uint32_t i = 0; i < sample_count; ++i) {
            int16_t pcm = convert_raw_sample(s_raw_samples[i], &config);
            pcm = apply_dc_block(pcm, &dc_state);
            pcm = apply_noise_gate_smoothing(pcm, &config, &gate_state);
            s_pcm16_16k[i] = apply_mic_gain(pcm, &config);
        }

        uint32_t out_count = 0;
        for (uint32_t i = 0; i < sample_count; ++i) {
            int16_t out_sample = 0;
            if (decimate_to_8k(s_pcm16_16k[i], &decimator_state, &out_sample)) {
                s_pcm16_8k[out_count++] = out_sample;
            }
        }

        if (out_count > 0) {
            size_t out_bytes = out_count * sizeof(s_pcm16_8k[0]);
            BaseType_t ok = xRingbufferSend(s_pcm_ringbuf, s_pcm16_8k, out_bytes, 0);
            if (ok != pdTRUE) {
                ESP_LOGW(TAG, "audio ringbuffer full, dropping %u bytes", (unsigned)out_bytes);
            } else {
                hfp_audio_source_notify_ready();
            }
        }

        TickType_t now = xTaskGetTickCount();
        if ((now - last_print) >= pdMS_TO_TICKS(HFP_AUDIO_PRINT_INTERVAL_MS)) {
            audio_capture_stats_t stats = audio_math_compute_stats(s_pcm16_16k, sample_count);
            ESP_LOGI(TAG, "mic peak=%d rms=%" PRIu32 " src_samples=%" PRIu32 " ptt=%s",
                     stats.peak, stats.rms, sample_count, s_ptt_pressed ? "on" : "off");
            last_print = now;
        }
    }
}

esp_err_t hfp_audio_source_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }

    s_pcm_ringbuf = xRingbufferCreate(HFP_AUDIO_RINGBUF_BYTES, RINGBUF_TYPE_BYTEBUF);
    ESP_RETURN_ON_FALSE(s_pcm_ringbuf != NULL, ESP_ERR_NO_MEM, TAG, "failed to create audio ringbuffer");

    ESP_RETURN_ON_ERROR(load_config_from_nvs(), TAG, "load audio config failed");

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 256;

    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_rx_chan), TAG, "i2s_new_channel failed");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(HFP_AUDIO_I2S_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = INMP441_BCLK_GPIO,
            .ws = INMP441_WS_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din = INMP441_DIN_GPIO,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_rx_chan, &std_cfg),
                        TAG, "i2s_channel_init_std_mode failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_rx_chan), TAG, "i2s_channel_enable failed");

    BaseType_t task_ok = xTaskCreate(
        audio_task,
        "hfp_audio",
        HFP_AUDIO_TASK_STACK,
        NULL,
        HFP_AUDIO_TASK_PRIORITY,
        &s_audio_task);
    ESP_RETURN_ON_FALSE(task_ok == pdPASS, ESP_ERR_NO_MEM, TAG, "failed to create audio task");

    s_initialized = true;
    ESP_LOGI(TAG, "I2S mic initialized: %d Hz -> HFP CVSD %d Hz PCM, BCLK=%d WS=%d DIN=%d",
             HFP_AUDIO_I2S_SAMPLE_RATE_HZ,
             HFP_AUDIO_CVSD_SAMPLE_RATE_HZ,
             INMP441_BCLK_GPIO,
             INMP441_WS_GPIO,
             INMP441_DIN_GPIO);
    return ESP_OK;
}

esp_err_t hfp_audio_source_start(void)
{
    ESP_RETURN_ON_FALSE(s_initialized, ESP_ERR_INVALID_STATE, TAG, "audio source not initialized");
    ringbuf_clear();
    s_running = true;
    ESP_LOGI(TAG, "HFP audio source started");
    return ESP_OK;
}

esp_err_t hfp_audio_source_stop(void)
{
    ESP_RETURN_ON_FALSE(s_initialized, ESP_ERR_INVALID_STATE, TAG, "audio source not initialized");
    s_running = false;
    ringbuf_clear();
    ESP_LOGI(TAG, "HFP audio source stopped");
    return ESP_OK;
}

void hfp_audio_source_set_ptt(bool pressed)
{
    s_ptt_pressed = pressed;
    s_filter_reset_requested = true;
    if (!pressed) {
        ringbuf_clear();
    }
    ESP_LOGI(TAG, "PTT audio gate %s", pressed ? "open" : "muted");
}

bool hfp_audio_source_is_running(void)
{
    return s_running;
}

uint32_t hfp_audio_source_read(uint8_t *dst, uint32_t len)
{
    if (dst == NULL || len == 0) {
        return 0;
    }

    if (!s_running) {
        memset(dst, 0, len);
        mix_pending_tones(dst, len);
        return len;
    }

    if (!s_ptt_pressed || s_pcm_ringbuf == NULL) {
        memset(dst, 0, len);
        mix_pending_tones(dst, len);
        return len;
    }

    uint32_t copied = 0;
    while (copied < len) {
        size_t item_size = 0;
        uint8_t *item = xRingbufferReceiveUpTo(s_pcm_ringbuf, &item_size, 0, len - copied);
        if (item == NULL || item_size == 0) {
            break;
        }
        memcpy(dst + copied, item, item_size);
        copied += item_size;
        vRingbufferReturnItem(s_pcm_ringbuf, item);
    }

    if (copied < len) {
        memset(dst + copied, 0, len - copied);
    }

    mix_pending_tones(dst, len);

    return len;
}

void hfp_audio_source_inject_tone_start(void)
{
    tone_event_push(TONE_EVENT_START);
}

void hfp_audio_source_inject_tone_stop(void)
{
    tone_event_push(TONE_EVENT_STOP);
}

void hfp_audio_source_inject_tone_a(void)
{
    tone_event_push(TONE_EVENT_A);
}

void hfp_audio_source_inject_tone_b(void)
{
    tone_event_push(TONE_EVENT_B);
}

void hfp_audio_source_inject_tone_c(void)
{
    tone_event_push(TONE_EVENT_C);
}

void hfp_audio_source_get_config(hfp_audio_source_config_t *config)
{
    if (config == NULL) {
        return;
    }
    *config = current_config();
}

esp_err_t hfp_audio_source_set_config(const hfp_audio_source_config_t *config)
{
    ESP_RETURN_ON_FALSE(config != NULL, ESP_ERR_INVALID_ARG, TAG, "config is null");

    hfp_audio_source_config_t sanitized = {
        .mic_gain_q8 = clamp_u16(config->mic_gain_q8, CFG_MIN_MIC_GAIN_Q8, CFG_MAX_MIC_GAIN_Q8),
        .noise_gate = clamp_u16(config->noise_gate, 0, CFG_MAX_NOISE_GATE),
        .tone_gain_q8 = clamp_u16(config->tone_gain_q8, CFG_MIN_TONE_GAIN_Q8, CFG_MAX_TONE_GAIN_Q8),
        .sample_shift = (uint8_t)clamp_u16(config->sample_shift, CFG_MIN_SAMPLE_SHIFT, CFG_MAX_SAMPLE_SHIFT),
    };

    portENTER_CRITICAL(&s_config_lock);
    s_config = sanitized;
    portEXIT_CRITICAL(&s_config_lock);

    log_config("audio config set", &sanitized);
    return ESP_OK;
}

esp_err_t hfp_audio_source_reset_config(void)
{
    hfp_audio_source_config_t defaults = {
        .mic_gain_q8 = CFG_DEFAULT_MIC_GAIN_Q8,
        .noise_gate = CFG_DEFAULT_NOISE_GATE,
        .tone_gain_q8 = CFG_DEFAULT_TONE_GAIN_Q8,
        .sample_shift = CFG_DEFAULT_SAMPLE_SHIFT,
    };
    ESP_RETURN_ON_ERROR(hfp_audio_source_set_config(&defaults), TAG, "set default audio config failed");
    return ESP_OK;
}

esp_err_t hfp_audio_source_save_config(void)
{
    hfp_audio_source_config_t config = current_config();
    nvs_handle_t nvs;
    esp_err_t ret = nvs_open(CFG_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    ESP_RETURN_ON_ERROR(ret, TAG, "open audio config nvs failed");

    ret = nvs_set_u16(nvs, CFG_NVS_KEY_GAIN_Q8, config.mic_gain_q8);
    if (ret == ESP_OK) {
        ret = nvs_set_u16(nvs, CFG_NVS_KEY_GATE, config.noise_gate);
    }
    if (ret == ESP_OK) {
        ret = nvs_set_u16(nvs, CFG_NVS_KEY_TONE_Q8, config.tone_gain_q8);
    }
    if (ret == ESP_OK) {
        ret = nvs_set_u8(nvs, CFG_NVS_KEY_SHIFT, config.sample_shift);
    }
    if (ret == ESP_OK) {
        ret = nvs_commit(nvs);
    }
    nvs_close(nvs);

    ESP_RETURN_ON_ERROR(ret, TAG, "save audio config failed");
    log_config("audio config saved", &config);
    return ESP_OK;
}

esp_err_t hfp_audio_source_set_sr_enabled(bool enabled)
{
    if (enabled) {
        ESP_LOGW(TAG, "ESP-SR removed from this build; staying on legacy path");
        return ESP_ERR_NOT_SUPPORTED;
    }

    ESP_LOGI(TAG, "ESP-SR runtime mode disabled; legacy audio path active");
    return ESP_OK;
}

bool hfp_audio_source_is_sr_enabled(void)
{
    return false;
}

bool hfp_audio_source_is_sr_initialized(void)
{
    return false;
}

void hfp_audio_source_set_ready_callback(hfp_audio_source_ready_cb_t callback)
{
    s_ready_cb = callback;
}

void hfp_audio_source_notify_ready(void)
{
    if (s_ready_cb != NULL) {
        s_ready_cb();
    }
}


