#include "bt_hid_keyboard.h"

#include <string.h>

#include "esp_check.h"
#include "esp_gap_bt_api.h"
#include "esp_hidd.h"
#include "esp_log.h"

static const char *TAG = "bt_hid_keyboard";

#define HID_BATTERY_LEVEL 80

static esp_hidd_dev_t *s_hid_dev;
static bool s_connected;
static bool s_started;

static const uint8_t s_keyboard_report_map[] = {
    0x05, 0x01,        // Usage Page (Generic Desktop)
    0x09, 0x06,        // Usage (Keyboard)
    0xA1, 0x01,        // Collection (Application)
    0x05, 0x07,        // Usage Page (Keyboard/Keypad)
    0x19, 0xE0,        // Usage Minimum (Left Control)
    0x29, 0xE7,        // Usage Maximum (Right GUI)
    0x15, 0x00,        // Logical Minimum (0)
    0x25, 0x01,        // Logical Maximum (1)
    0x75, 0x01,        // Report Size (1)
    0x95, 0x08,        // Report Count (8)
    0x81, 0x02,        // Input (Data, Variable, Absolute)
    0x95, 0x01,        // Report Count (1)
    0x75, 0x08,        // Report Size (8)
    0x81, 0x03,        // Input (Constant)
    0x95, 0x05,        // Report Count (5)
    0x75, 0x01,        // Report Size (1)
    0x05, 0x08,        // Usage Page (LEDs)
    0x19, 0x01,        // Usage Minimum (Num Lock)
    0x29, 0x05,        // Usage Maximum (Kana)
    0x91, 0x02,        // Output (Data, Variable, Absolute)
    0x95, 0x01,        // Report Count (1)
    0x75, 0x03,        // Report Size (3)
    0x91, 0x03,        // Output (Constant)
    0x95, 0x06,        // Report Count (6)
    0x75, 0x08,        // Report Size (8)
    0x15, 0x00,        // Logical Minimum (0)
    0x25, 0x65,        // Logical Maximum (101)
    0x05, 0x07,        // Usage Page (Keyboard/Keypad)
    0x19, 0x00,        // Usage Minimum (Reserved)
    0x29, 0x65,        // Usage Maximum (Keyboard Application)
    0x81, 0x00,        // Input (Data, Array, Absolute)
    0xC0,              // End Collection
};

static esp_hid_raw_report_map_t s_report_maps[] = {
    {
        .data = s_keyboard_report_map,
        .len = sizeof(s_keyboard_report_map),
    },
};

static esp_hid_device_config_t s_hid_config = {
    .vendor_id = 0x16C0,
    .product_id = 0x05DF,
    .version = 0x0100,
    .device_name = BT_HID_KEYBOARD_DEVICE_NAME,
    .manufacturer_name = "AirMic",
    .serial_number = "ESP32-HFP-PTT-001",
    .report_maps = s_report_maps,
    .report_maps_len = 1,
};

static void hidd_event_handler(void *handler_args, esp_event_base_t base, int32_t id, void *event_data)
{
    (void)handler_args;
    (void)base;

    esp_hidd_event_t event = (esp_hidd_event_t)id;
    esp_hidd_event_data_t *param = (esp_hidd_event_data_t *)event_data;

    switch (event) {
    case ESP_HIDD_START_EVENT:
        if (param != NULL && param->start.status == ESP_OK) {
            s_started = true;
            ESP_LOGI(TAG, "Classic BT HID started");
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
        } else {
            ESP_LOGE(TAG, "Classic BT HID start failed: %s",
                     param ? esp_err_to_name(param->start.status) : "no_param");
        }
        break;
    case ESP_HIDD_CONNECT_EVENT:
        if (param != NULL && param->connect.status == ESP_OK) {
            s_connected = true;
            ESP_LOGI(TAG, "Classic BT HID host connected");
        } else {
            ESP_LOGE(TAG, "Classic BT HID connect failed: %s",
                     param ? esp_err_to_name(param->connect.status) : "no_param");
        }
        break;
    case ESP_HIDD_DISCONNECT_EVENT:
        s_connected = false;
        ESP_LOGI(TAG, "Classic BT HID host disconnected: reason=%d status=%s",
                 param ? param->disconnect.reason : -1,
                 param ? esp_err_to_name(param->disconnect.status) : "no_param");
        esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
        break;
    case ESP_HIDD_PROTOCOL_MODE_EVENT:
        ESP_LOGI(TAG, "Classic BT HID protocol mode: %s",
                 param && param->protocol_mode.protocol_mode ? "report" : "boot");
        break;
    case ESP_HIDD_OUTPUT_EVENT:
        ESP_LOGI(TAG, "Classic BT HID output report: id=%u len=%u",
                 param ? param->output.report_id : 0,
                 param ? param->output.length : 0);
        break;
    case ESP_HIDD_STOP_EVENT:
        s_started = false;
        s_connected = false;
        ESP_LOGI(TAG, "Classic BT HID stopped");
        break;
    default:
        break;
    }
}

esp_err_t bt_hid_keyboard_init(void)
{
    ESP_RETURN_ON_ERROR(esp_hidd_dev_init(&s_hid_config, ESP_HID_TRANSPORT_BT,
                                          hidd_event_handler, &s_hid_dev),
                        TAG, "Classic BT HID device init failed");
    ESP_RETURN_ON_ERROR(esp_hidd_dev_battery_set(s_hid_dev, HID_BATTERY_LEVEL),
                        TAG, "Classic BT HID battery set failed");

    ESP_LOGI(TAG, "Classic BT HID keyboard initialized, device_name='%s'",
             BT_HID_KEYBOARD_DEVICE_NAME);
    return ESP_OK;
}

static esp_err_t send_keyboard_report(uint8_t modifier)
{
    if (s_hid_dev == NULL || !s_started || !s_connected || !esp_hidd_dev_connected(s_hid_dev)) {
        ESP_LOGW(TAG, "HID report not sent: host not connected");
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t report[8] = {
        modifier,
        0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    };

    esp_err_t ret = esp_hidd_dev_input_set(s_hid_dev, 0, 0, report, sizeof(report));
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "HID keyboard report sent: modifier=0x%02x", modifier);
    } else {
        ESP_LOGE(TAG, "HID keyboard report failed: modifier=0x%02x err=%s",
                 modifier, esp_err_to_name(ret));
    }

    return ret;
}

esp_err_t bt_hid_keyboard_send_right_alt_down(void)
{
    return send_keyboard_report(BT_HID_RIGHT_ALT_MODIFIER);
}

esp_err_t bt_hid_keyboard_send_all_keys_up(void)
{
    return send_keyboard_report(0x00);
}

bool bt_hid_keyboard_is_connected(void)
{
    return s_connected && s_hid_dev != NULL && esp_hidd_dev_connected(s_hid_dev);
}
