#include "bt_hfp_hf.h"
#include "button_ptt.h"
#include "board_config.h"
#include "hfp_audio_source.h"

#include <stdio.h>

#include "esp_check.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdint.h>
#include <string.h>

static const char *TAG = "airmic_hfp";

#define STATUS_LED_TASK_STACK_BYTES   4096
#define TONE_SERIAL_TASK_STACK_BYTES  4096

typedef enum {
    RECORD_MODE_PTT = 0,
    RECORD_MODE_ALWAYS = 1,
} record_mode_t;

static record_mode_t s_record_mode = RECORD_MODE_PTT;
static bool s_active_button_valid;
static button_input_id_t s_active_button = BUTTON_INPUT_PTT;

static const char *button_label(button_input_id_t input_id)
{
    switch (input_id) {
    case BUTTON_INPUT_PTT:
        return "START";
    case BUTTON_INPUT_TONE_A:
        return "Tone A";
    case BUTTON_INPUT_TONE_B:
        return "Tone B";
    case BUTTON_INPUT_TONE_C:
        return "Tone C";
    default:
        return "Unknown";
    }
}

static void inject_tone_for_button(button_input_id_t input_id)
{
    switch (input_id) {
    case BUTTON_INPUT_PTT:
        hfp_audio_source_inject_tone_start();
        break;
    case BUTTON_INPUT_TONE_A:
        hfp_audio_source_inject_tone_a();
        break;
    case BUTTON_INPUT_TONE_B:
        hfp_audio_source_inject_tone_b();
        break;
    case BUTTON_INPUT_TONE_C:
        hfp_audio_source_inject_tone_c();
        break;
    default:
        break;
    }
}

static void request_audio_connect_if_needed(void)
{
    if (bt_hfp_hf_is_slc_connected() &&
        !bt_hfp_hf_is_audio_connected() &&
        !bt_hfp_hf_is_audio_connecting()) {
        esp_err_t ret = bt_hfp_hf_connect_audio();
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "connect HFP audio failed: %s", esp_err_to_name(ret));
        }
    }
}

static const char *reset_reason_name(esp_reset_reason_t reason)
{
    switch (reason) {
    case ESP_RST_UNKNOWN:   return "unknown";
    case ESP_RST_POWERON:   return "poweron";
    case ESP_RST_EXT:       return "ext";
    case ESP_RST_SW:        return "sw";
    case ESP_RST_PANIC:     return "panic";
    case ESP_RST_INT_WDT:   return "int_wdt";
    case ESP_RST_TASK_WDT:  return "task_wdt";
    case ESP_RST_WDT:       return "wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    case ESP_RST_BROWNOUT:  return "brownout";
    case ESP_RST_SDIO:      return "sdio";
    default:                return "other";
    }
}

static void log_boot_diagnostics(void)
{
    esp_reset_reason_t reason = esp_reset_reason();
    ESP_LOGI(TAG,
             "reset reason=%s(%d) heap8=%u internal_free=%u internal_largest=%u",
             reset_reason_name(reason),
             (int)reason,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
}

static void apply_record_mode(void)
{
    bool gate_open = (s_record_mode == RECORD_MODE_ALWAYS) || s_active_button_valid;
    hfp_audio_source_set_ptt(gate_open);
    ESP_LOGI(TAG, "record mode=%s",
             s_record_mode == RECORD_MODE_ALWAYS ? "always" : "ptt");
}

static void begin_button_session(button_input_id_t input_id)
{
    s_active_button = input_id;
    s_active_button_valid = true;
    if (s_record_mode == RECORD_MODE_PTT) {
        hfp_audio_source_set_ptt(true);
    }
    inject_tone_for_button(input_id);
    request_audio_connect_if_needed();
}

static void end_button_session(button_input_id_t input_id)
{
    if (!s_active_button_valid || s_active_button != input_id) {
        return;
    }
    s_active_button_valid = false;
    if (s_record_mode == RECORD_MODE_PTT) {
        hfp_audio_source_set_ptt(false);
    }
    hfp_audio_source_inject_tone_stop();
}

static void log_audio_config(void)
{
    hfp_audio_source_config_t config;
    hfp_audio_source_get_config(&config);
    ESP_LOGI(TAG, "cfg gain_q8=%u gain=%.2fx gate=%u tone_q8=%u tone=%.2fx shift=%u sr=%s sr_init=%s",
             config.mic_gain_q8,
             (double)config.mic_gain_q8 / 256.0,
             config.noise_gate,
             config.tone_gain_q8,
             (double)config.tone_gain_q8 / 256.0,
             config.sample_shift,
             hfp_audio_source_is_sr_enabled() ? "on" : "off",
             hfp_audio_source_is_sr_initialized() ? "yes" : "no");
}

static void handle_cfg_command(char *line)
{
    char key[16] = {0};
    int value = 0;
    hfp_audio_source_config_t config;
    hfp_audio_source_get_config(&config);

    if (sscanf(line, "cfg %15s %d", key, &value) == 2) {
        if (strcmp(key, "gain") == 0) {
            config.mic_gain_q8 = (uint16_t)value;
            ESP_ERROR_CHECK_WITHOUT_ABORT(hfp_audio_source_set_config(&config));
            log_audio_config();
            return;
        }
        if (strcmp(key, "gate") == 0) {
            config.noise_gate = (uint16_t)value;
            ESP_ERROR_CHECK_WITHOUT_ABORT(hfp_audio_source_set_config(&config));
            log_audio_config();
            return;
        }
        if (strcmp(key, "tone") == 0) {
            config.tone_gain_q8 = (uint16_t)value;
            ESP_ERROR_CHECK_WITHOUT_ABORT(hfp_audio_source_set_config(&config));
            log_audio_config();
            return;
        }
        if (strcmp(key, "shift") == 0) {
            config.sample_shift = (uint8_t)value;
            ESP_ERROR_CHECK_WITHOUT_ABORT(hfp_audio_source_set_config(&config));
            log_audio_config();
            return;
        }
    }

    if (strcmp(line, "cfg show") == 0 || strcmp(line, "show") == 0) {
        log_audio_config();
        ESP_LOGI(TAG, "record mode=%s", s_record_mode == RECORD_MODE_ALWAYS ? "always" : "ptt");
        return;
    }
    if (strcmp(line, "sr on") == 0) {
        esp_err_t ret = hfp_audio_source_set_sr_enabled(true);
        ESP_LOGI(TAG, "sr on result: %s", esp_err_to_name(ret));
        log_audio_config();
        return;
    }
    if (strcmp(line, "sr off") == 0) {
        esp_err_t ret = hfp_audio_source_set_sr_enabled(false);
        ESP_LOGI(TAG, "sr off result: %s", esp_err_to_name(ret));
        log_audio_config();
        return;
    }
    if (strcmp(line, "sr show") == 0) {
        log_audio_config();
        return;
    }
    if (strcmp(line, "mode record ptt") == 0) {
        s_record_mode = RECORD_MODE_PTT;
        apply_record_mode();
        return;
    }
    if (strcmp(line, "mode record always") == 0) {
        s_record_mode = RECORD_MODE_ALWAYS;
        apply_record_mode();
        return;
    }
    if (strcmp(line, "audio connect") == 0) {
        esp_err_t ret = bt_hfp_hf_connect_audio();
        ESP_LOGI(TAG, "audio connect result: %s", esp_err_to_name(ret));
        return;
    }
    if (strcmp(line, "audio disconnect") == 0) {
        esp_err_t ret = bt_hfp_hf_disconnect_audio();
        ESP_LOGI(TAG, "audio disconnect result: %s", esp_err_to_name(ret));
        return;
    }
    if (strcmp(line, "cfg save") == 0 || strcmp(line, "save") == 0) {
        esp_err_t ret = hfp_audio_source_save_config();
        ESP_LOGI(TAG, "cfg save result: %s", esp_err_to_name(ret));
        log_audio_config();
        return;
    }
    if (strcmp(line, "cfg reset") == 0 || strcmp(line, "reset") == 0) {
        esp_err_t ret = hfp_audio_source_reset_config();
        ESP_LOGI(TAG, "cfg reset result: %s", esp_err_to_name(ret));
        log_audio_config();
        return;
    }

    ESP_LOGW(TAG, "unknown cfg command: '%s'", line);
    ESP_LOGI(TAG, "cfg/audio commands: cfg gain <q8>, cfg gate <pcm>, cfg tone <q8>, cfg shift <bits>, cfg show, cfg save, cfg reset, sr on/off/show (compat), mode record ptt, mode record always, audio connect, audio disconnect");
}

static void status_led_task(void *arg)
{
    (void)arg;

    gpio_config_t led_cfg = {
        .pin_bit_mask = 1ULL << STATUS_LED_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&led_cfg));

    bool led_on = false;
    while (true) {
        if (bt_hfp_hf_is_audio_connected()) {
            gpio_set_level(STATUS_LED_GPIO, 1);
            vTaskDelay(pdMS_TO_TICKS(250));
        } else if (bt_hfp_hf_is_slc_connected()) {
            gpio_set_level(STATUS_LED_GPIO, led_on ? 1 : 0);
            led_on = !led_on;
            vTaskDelay(pdMS_TO_TICKS(250));
        } else {
            gpio_set_level(STATUS_LED_GPIO, led_on ? 1 : 0);
            led_on = !led_on;
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
}

static void serial_tone_task(void *arg)
{
    (void)arg;

    ESP_LOGI(TAG, "Serial commands: s=START, e=STOP, a=Tone A, b=Tone B, c=Tone C");
    ESP_LOGI(TAG, "Audio cfg commands: cfg gain <q8>, cfg gate <pcm>, cfg tone <q8>, cfg shift <bits>, cfg show, cfg save, cfg reset, sr on/off/show (compat)");
    ESP_LOGI(TAG, "Record mode commands: mode record ptt, mode record always");
    ESP_LOGI(TAG, "HFP audio commands: audio connect, audio disconnect");

    char line[64];
    size_t line_len = 0;

    while (true) {
        int ch = getchar();
        if (ch == EOF) {
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        if (ch == '\r' || ch == '\n') {
            if (line_len > 0) {
                line[line_len] = '\0';
                if (strcmp(line, "s") == 0 || strcmp(line, "S") == 0) {
                    ESP_LOGI(TAG, "serial command: inject START tone");
                    hfp_audio_source_inject_tone_start();
                } else if (strcmp(line, "e") == 0 || strcmp(line, "E") == 0) {
                    ESP_LOGI(TAG, "serial command: inject STOP tone");
                    hfp_audio_source_inject_tone_stop();
                } else if (strcmp(line, "a") == 0 || strcmp(line, "A") == 0) {
                    ESP_LOGI(TAG, "serial command: inject Tone A");
                    hfp_audio_source_inject_tone_a();
                } else if (strcmp(line, "b") == 0 || strcmp(line, "B") == 0) {
                    ESP_LOGI(TAG, "serial command: inject Tone B");
                    hfp_audio_source_inject_tone_b();
                } else if (strcmp(line, "c") == 0 || strcmp(line, "C") == 0) {
                    ESP_LOGI(TAG, "serial command: inject Tone C");
                    hfp_audio_source_inject_tone_c();
                } else {
                    handle_cfg_command(line);
                }
                line_len = 0;
            }
            continue;
        }

        if (line_len < sizeof(line) - 1) {
            line[line_len++] = (char)ch;
        } else {
            line_len = 0;
            ESP_LOGW(TAG, "serial command too long, discarded");
        }
    }
}

static esp_err_t init_nvs(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "NVS erase failed");
        ret = nvs_flash_init();
    }
    ESP_RETURN_ON_ERROR(ret, TAG, "NVS init failed");
    ESP_LOGI(TAG, "NVS initialized");
    return ESP_OK;
}

static void ptt_changed(button_input_id_t input_id, bool pressed, void *user_ctx)
{
    (void)user_ctx;

    ESP_LOGI(TAG, "button=%d %s, SLC=%s audio=%s",
             (int)input_id,
             pressed ? "pressed" : "released",
             bt_hfp_hf_is_slc_connected() ? "connected" : "no",
             bt_hfp_hf_is_audio_connected() ? "connected" : "no");

    if (pressed) {
        if (s_active_button_valid && s_active_button != input_id) {
            ESP_LOGW(TAG, "ignore %s press while %s session active",
                     button_label(input_id), button_label(s_active_button));
            return;
        }
        if (s_active_button_valid && s_active_button == input_id) {
            ESP_LOGI(TAG, "%s press ignored: session already active", button_label(input_id));
            return;
        }
        begin_button_session(input_id);
        return;
    }

    if (!s_active_button_valid) {
        ESP_LOGI(TAG, "%s release ignored: no active session", button_label(input_id));
        return;
    }
    if (s_active_button != input_id) {
        ESP_LOGI(TAG, "%s release ignored: active session belongs to %s",
                 button_label(input_id), button_label(s_active_button));
        return;
    }
    end_button_session(input_id);
}

void app_main(void)
{
    ESP_LOGI(TAG, "ESP32 AirMic HFP HF demo booting");
    log_boot_diagnostics();
    ESP_LOGI(TAG, "Mic: INMP441 I2S %d Hz -> HFP CVSD %d Hz PCM",
             HFP_AUDIO_I2S_SAMPLE_RATE_HZ,
             HFP_AUDIO_CVSD_SAMPLE_RATE_HZ);

    ESP_ERROR_CHECK(init_nvs());
    ESP_ERROR_CHECK(hfp_audio_source_init());
    apply_record_mode();
    ESP_ERROR_CHECK(bt_hfp_hf_init());
    ESP_ERROR_CHECK(button_ptt_init(ptt_changed, NULL));

    BaseType_t led_task_ok = xTaskCreate(status_led_task, "hfp_led", STATUS_LED_TASK_STACK_BYTES, NULL, 2, NULL);
    ESP_ERROR_CHECK(led_task_ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);

    BaseType_t serial_task_ok = xTaskCreate(serial_tone_task, "tone_serial", TONE_SERIAL_TASK_STACK_BYTES, NULL, 2, NULL);
    ESP_ERROR_CHECK(serial_task_ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);

    ESP_LOGI(TAG, "Ready. Pair '%s' for HFP microphone. GPIO%d=Start, GPIO%d=Tone A, GPIO%d=Tone B, GPIO%d=Tone C.",
             BT_HFP_HF_DEVICE_NAME,
             PTT_BUTTON_GPIO,
             TONE_A_BUTTON_GPIO,
             TONE_B_BUTTON_GPIO,
             TONE_C_BUTTON_GPIO);
    ESP_LOGI(TAG, "Status LED GPIO%d: slow blink=no SLC, fast blink=SLC only, solid=HFP audio connected",
             STATUS_LED_GPIO);
}

