#pragma once

#include <stdbool.h>

#include "esp_err.h"

#define BT_HFP_HF_DEVICE_NAME "ESP32-AirMic-HFP"

typedef enum {
    BT_HFP_HF_SLC_DISCONNECTED = 0,
    BT_HFP_HF_SLC_CONNECTED,
} bt_hfp_hf_slc_state_t;

typedef enum {
    BT_HFP_HF_AUDIO_DISCONNECTED = 0,
    BT_HFP_HF_AUDIO_CONNECTED,
} bt_hfp_hf_audio_state_t;

esp_err_t bt_hfp_hf_init(void);
esp_err_t bt_hfp_hf_connect_audio(void);
esp_err_t bt_hfp_hf_disconnect_audio(void);
bool bt_hfp_hf_is_slc_connected(void);
bool bt_hfp_hf_is_audio_connected(void);
