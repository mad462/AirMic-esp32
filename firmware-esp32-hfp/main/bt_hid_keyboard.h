#pragma once

#include <stdbool.h>

#include "esp_err.h"

#define BT_HID_KEYBOARD_DEVICE_NAME "ESP32-AirMic-HFP"
#define BT_HID_RIGHT_ALT_MODIFIER   0x40

esp_err_t bt_hid_keyboard_init(void);
esp_err_t bt_hid_keyboard_send_right_alt_down(void);
esp_err_t bt_hid_keyboard_send_all_keys_up(void);
bool bt_hid_keyboard_is_connected(void);
