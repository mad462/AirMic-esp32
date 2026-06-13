#pragma once

#include <stdbool.h>

#include "esp_err.h"

typedef enum {
    BUTTON_INPUT_PTT = 0,
    BUTTON_INPUT_TONE_A,
    BUTTON_INPUT_TONE_B,
    BUTTON_INPUT_TONE_C,
} button_input_id_t;

typedef void (*button_ptt_callback_t)(button_input_id_t input_id, bool pressed, void *user_ctx);

esp_err_t button_ptt_init(button_ptt_callback_t callback, void *user_ctx);
