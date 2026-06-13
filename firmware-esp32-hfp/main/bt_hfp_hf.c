#include "bt_hfp_hf.h"

#include <inttypes.h>
#include <string.h>

#include "esp_bt.h"
#include "esp_bt_device.h"
#include "esp_bt_main.h"
#include "esp_check.h"
#include "esp_gap_bt_api.h"
#include "esp_hf_client_api.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hfp_audio_source.h"
#include "nvs.h"

static const char *TAG = "bt_hfp_hf";

#define HFP_SLC_AUTOCONNECT_DELAY_MS     3000
#define HFP_SLC_AUTOCONNECT_INTERVAL_MS  5000
#define HFP_NVS_NAMESPACE                "airmic_bt"
#define HFP_NVS_KEY_PEER_BDA             "peer_bda"

static esp_bd_addr_t s_peer_bda;
static bool s_has_peer_bda;
static esp_bd_addr_t s_preferred_bda;
static bool s_has_preferred_bda;
static bool s_slc_connected;
static bool s_audio_connected;
static esp_hf_client_connection_state_t s_connection_state;
static uint16_t s_sync_conn_handle;
static TaskHandle_t s_slc_autoconnect_task;

static const char *connection_state_str(esp_hf_client_connection_state_t state)
{
    switch (state) {
    case ESP_HF_CLIENT_CONNECTION_STATE_DISCONNECTED:
        return "disconnected";
    case ESP_HF_CLIENT_CONNECTION_STATE_CONNECTING:
        return "connecting";
    case ESP_HF_CLIENT_CONNECTION_STATE_CONNECTED:
        return "connected";
    case ESP_HF_CLIENT_CONNECTION_STATE_SLC_CONNECTED:
        return "slc_connected";
    case ESP_HF_CLIENT_CONNECTION_STATE_DISCONNECTING:
        return "disconnecting";
    default:
        return "unknown";
    }
}

static const char *audio_state_str(esp_hf_client_audio_state_t state)
{
    switch (state) {
    case ESP_HF_CLIENT_AUDIO_STATE_DISCONNECTED:
        return "disconnected";
    case ESP_HF_CLIENT_AUDIO_STATE_CONNECTING:
        return "connecting";
    case ESP_HF_CLIENT_AUDIO_STATE_CONNECTED:
        return "connected_cvsd";
    case ESP_HF_CLIENT_AUDIO_STATE_CONNECTED_MSBC:
        return "connected_msbc";
    default:
        return "unknown";
    }
}

static char *bda2str(const uint8_t *bda, char *str, size_t size)
{
    if (bda == NULL || str == NULL || size < 18) {
        return NULL;
    }

    snprintf(str, size, "%02x:%02x:%02x:%02x:%02x:%02x",
             bda[0], bda[1], bda[2], bda[3], bda[4], bda[5]);
    return str;
}

static void hfp_audio_ready(void)
{
    if (s_audio_connected) {
        esp_hf_client_outgoing_data_ready();
    }
}

static esp_err_t save_preferred_bda_to_nvs(const esp_bd_addr_t bda)
{
    nvs_handle_t nvs;
    esp_err_t ret = nvs_open(HFP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (ret != ESP_OK) {
        return ret;
    }

    ret = nvs_set_blob(nvs, HFP_NVS_KEY_PEER_BDA, bda, ESP_BD_ADDR_LEN);
    if (ret == ESP_OK) {
        ret = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return ret;
}

static void update_preferred_peer(const esp_bd_addr_t bda, const char *reason)
{
    if (bda == NULL) {
        return;
    }

    bool changed = !s_has_preferred_bda || memcmp(s_preferred_bda, bda, ESP_BD_ADDR_LEN) != 0;
    memcpy(s_preferred_bda, bda, ESP_BD_ADDR_LEN);
    s_has_preferred_bda = true;

    if (changed) {
        char bda_str[18] = {0};
        esp_err_t ret = save_preferred_bda_to_nvs(bda);
        ESP_LOGI(TAG, "preferred peer updated from %s: %s save=%s",
                 reason != NULL ? reason : "unknown",
                 bda2str(bda, bda_str, sizeof(bda_str)),
                 esp_err_to_name(ret));
    }
}

static void load_preferred_peer(void)
{
    nvs_handle_t nvs;
    esp_err_t ret = nvs_open(HFP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (ret != ESP_OK) {
        return;
    }

    size_t len = ESP_BD_ADDR_LEN;
    ret = nvs_get_blob(nvs, HFP_NVS_KEY_PEER_BDA, s_preferred_bda, &len);
    nvs_close(nvs);
    if (ret == ESP_OK && len == ESP_BD_ADDR_LEN) {
        s_has_preferred_bda = true;
        char bda_str[18] = {0};
        ESP_LOGI(TAG, "loaded preferred peer from nvs: %s",
                 bda2str(s_preferred_bda, bda_str, sizeof(bda_str)));
    }
}

static void load_bonded_peer_fallback(void)
{
    if (s_has_preferred_bda) {
        return;
    }

    int dev_num = esp_bt_gap_get_bond_device_num();
    if (dev_num <= 0) {
        ESP_LOGI(TAG, "no bonded BT devices stored yet");
        return;
    }

    esp_bd_addr_t *dev_list = calloc((size_t)dev_num, sizeof(esp_bd_addr_t));
    if (dev_list == NULL) {
        ESP_LOGW(TAG, "alloc bonded device list failed");
        return;
    }

    int requested = dev_num;
    esp_err_t ret = esp_bt_gap_get_bond_device_list(&requested, dev_list);
    if (ret == ESP_OK && requested > 0) {
        update_preferred_peer(dev_list[0], "bonded-list");
        ESP_LOGI(TAG, "bonded device fallback count=%d", requested);
    } else {
        ESP_LOGW(TAG, "load bonded device list failed: %s", esp_err_to_name(ret));
    }
    free(dev_list);
}

static void slc_autoconnect_task(void *arg)
{
    (void)arg;

    vTaskDelay(pdMS_TO_TICKS(HFP_SLC_AUTOCONNECT_DELAY_MS));
    while (true) {
        if (s_has_preferred_bda &&
            s_connection_state == ESP_HF_CLIENT_CONNECTION_STATE_DISCONNECTED) {
            char bda_str[18] = {0};
            ESP_LOGI(TAG, "auto requesting HFP SLC connection to %s",
                     bda2str(s_preferred_bda, bda_str, sizeof(bda_str)));
            esp_err_t ret = esp_hf_client_connect(s_preferred_bda);
            if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
                ESP_LOGW(TAG, "auto HFP SLC connect failed: %s", esp_err_to_name(ret));
            }
            vTaskDelay(pdMS_TO_TICKS(HFP_SLC_AUTOCONNECT_INTERVAL_MS));
        } else {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
}

static void hfp_incoming_data_cb(const uint8_t *buf, uint32_t len)
{
    (void)buf;
    (void)len;
}

static uint32_t hfp_outgoing_data_cb(uint8_t *buf, uint32_t len)
{
    return hfp_audio_source_read(buf, len);
}

static void hf_client_cb(esp_hf_client_cb_event_t event, esp_hf_client_cb_param_t *param)
{
    switch (event) {
    case ESP_HF_CLIENT_CONNECTION_STATE_EVT: {
        char bda[18] = {0};
        s_connection_state = param->conn_stat.state;
        ESP_LOGI(TAG, "HFP connection state=%s peer_feat=0x%" PRIx32 " chld_feat=0x%" PRIx32 " bda=%s",
                 connection_state_str(param->conn_stat.state),
                 param->conn_stat.peer_feat,
                 param->conn_stat.chld_feat,
                 bda2str(param->conn_stat.remote_bda, bda, sizeof(bda)));

        if (param->conn_stat.state == ESP_HF_CLIENT_CONNECTION_STATE_SLC_CONNECTED) {
            memcpy(s_peer_bda, param->conn_stat.remote_bda, ESP_BD_ADDR_LEN);
            s_has_peer_bda = true;
            s_slc_connected = true;
            update_preferred_peer(param->conn_stat.remote_bda, "slc");
            esp_hf_client_volume_update(ESP_HF_VOLUME_CONTROL_TARGET_MIC, 15);
        } else if (param->conn_stat.state == ESP_HF_CLIENT_CONNECTION_STATE_DISCONNECTED) {
            s_slc_connected = false;
            s_audio_connected = false;
            s_has_peer_bda = false;
            hfp_audio_source_stop();
            esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
        }
        break;
    }
    case ESP_HF_CLIENT_AUDIO_STATE_EVT:
        ESP_LOGI(TAG, "HFP audio state=%s handle=%u",
                 audio_state_str(param->audio_stat.state),
                 param->audio_stat.sync_conn_handle);

        if (param->audio_stat.state == ESP_HF_CLIENT_AUDIO_STATE_CONNECTED ||
            param->audio_stat.state == ESP_HF_CLIENT_AUDIO_STATE_CONNECTED_MSBC) {
            s_audio_connected = true;
            s_sync_conn_handle = param->audio_stat.sync_conn_handle;
            esp_hf_client_register_data_callback(hfp_incoming_data_cb, hfp_outgoing_data_cb);
            hfp_audio_source_start();
        } else if (param->audio_stat.state == ESP_HF_CLIENT_AUDIO_STATE_DISCONNECTED) {
            s_audio_connected = false;
            s_sync_conn_handle = 0;
            hfp_audio_source_stop();
            ESP_LOGW(TAG, "audio link dropped; waiting for manual reconnect/request");
        }
        break;
    case ESP_HF_CLIENT_PROF_STATE_EVT:
        ESP_LOGI(TAG, "HFP profile state=%d", param->prof_stat.state);
        break;
    case ESP_HF_CLIENT_VOLUME_CONTROL_EVT:
        ESP_LOGI(TAG, "HFP volume control target=%d volume=%d",
                 param->volume_control.type, param->volume_control.volume);
        break;
    case ESP_HF_CLIENT_AT_RESPONSE_EVT:
        ESP_LOGI(TAG, "HFP AT response code=%d cme=%d",
                 param->at_response.code, param->at_response.cme);
        break;
    case ESP_HF_CLIENT_PKT_STAT_NUMS_GET_EVT:
        ESP_LOGI(TAG, "SCO stats rx_total=%" PRIu32 " rx_err=%" PRIu32 " tx_total=%" PRIu32 " tx_discarded=%" PRIu32,
                 param->pkt_nums.rx_total,
                 param->pkt_nums.rx_err,
                 param->pkt_nums.tx_total,
                 param->pkt_nums.tx_discarded);
        break;
    default:
        ESP_LOGI(TAG, "HFP event=%d", event);
        break;
    }
}

static void gap_cb(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t *param)
{
    switch (event) {
    case ESP_BT_GAP_AUTH_CMPL_EVT:
        if (param->auth_cmpl.stat == ESP_BT_STATUS_SUCCESS) {
            char bda[18] = {0};
            ESP_LOGI(TAG, "BT auth success name='%s' bda=%s",
                     param->auth_cmpl.device_name,
                     bda2str(param->auth_cmpl.bda, bda, sizeof(bda)));
            update_preferred_peer(param->auth_cmpl.bda, "auth");
        } else {
            ESP_LOGE(TAG, "BT auth failed status=%d", param->auth_cmpl.stat);
        }
        break;
    case ESP_BT_GAP_PIN_REQ_EVT: {
        ESP_LOGI(TAG, "BT PIN request, replying 0000");
        esp_bt_pin_code_t pin_code = {'0', '0', '0', '0'};
        esp_bt_gap_pin_reply(param->pin_req.bda, true, 4, pin_code);
        break;
    }
    case ESP_BT_GAP_CFM_REQ_EVT:
        ESP_LOGI(TAG, "BT SSP numeric confirm: %" PRIu32, param->cfm_req.num_val);
        esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
        break;
    case ESP_BT_GAP_KEY_NOTIF_EVT:
        ESP_LOGI(TAG, "BT SSP passkey: %" PRIu32, param->key_notif.passkey);
        break;
    case ESP_BT_GAP_MODE_CHG_EVT:
        ESP_LOGI(TAG, "BT GAP mode changed: %d", param->mode_chg.mode);
        break;
    default:
        ESP_LOGD(TAG, "BT GAP event=%d", event);
        break;
    }
}

esp_err_t bt_hfp_hf_init(void)
{
    esp_err_t ret = esp_bt_controller_mem_release(ESP_BT_MODE_BLE);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_RETURN_ON_ERROR(ret, TAG, "release BLE memory failed");
    }

    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_bt_controller_init(&bt_cfg), TAG, "BT controller init failed");
    ESP_RETURN_ON_ERROR(esp_bt_controller_enable(ESP_BT_MODE_CLASSIC_BT), TAG, "BT controller enable failed");

    esp_bluedroid_config_t bluedroid_cfg = BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_bluedroid_init_with_cfg(&bluedroid_cfg), TAG, "Bluedroid init failed");
    ESP_RETURN_ON_ERROR(esp_bluedroid_enable(), TAG, "Bluedroid enable failed");

    ESP_RETURN_ON_ERROR(esp_bt_gap_register_callback(gap_cb), TAG, "GAP callback register failed");
    ESP_RETURN_ON_ERROR(esp_bt_gap_set_device_name(BT_HFP_HF_DEVICE_NAME), TAG, "set device name failed");

    esp_bt_sp_param_t param_type = ESP_BT_SP_IOCAP_MODE;
    esp_bt_io_cap_t iocap = ESP_BT_IO_CAP_NONE;
    ESP_RETURN_ON_ERROR(esp_bt_gap_set_security_param(param_type, &iocap, sizeof(iocap)),
                        TAG, "set SSP IO capability failed");

    esp_bt_pin_type_t pin_type = ESP_BT_PIN_TYPE_FIXED;
    esp_bt_pin_code_t pin_code = {'0', '0', '0', '0'};
    ESP_RETURN_ON_ERROR(esp_bt_gap_set_pin(pin_type, 4, pin_code), TAG, "set PIN failed");

    ESP_RETURN_ON_ERROR(esp_hf_client_register_callback(hf_client_cb), TAG, "HFP callback register failed");
    ESP_RETURN_ON_ERROR(esp_hf_client_init(), TAG, "HFP client init failed");

    hfp_audio_source_set_ready_callback(hfp_audio_ready);
    load_preferred_peer();
    load_bonded_peer_fallback();

    ESP_RETURN_ON_ERROR(esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE),
                        TAG, "set discoverable/connectable failed");

    char own_bda[18] = {0};
    ESP_LOGI(TAG, "Classic BT HFP HF initialized name='%s' bda=%s, controller=BR/EDR",
             BT_HFP_HF_DEVICE_NAME,
             bda2str(esp_bt_dev_get_address(), own_bda, sizeof(own_bda)));
    ESP_LOGI(TAG, "Pair from Windows Bluetooth settings once; firmware will try to reconnect bonded host after boot");

    if (s_slc_autoconnect_task == NULL) {
        BaseType_t task_ok = xTaskCreate(slc_autoconnect_task, "hfp_slc_reconn", 3072, NULL, 2, &s_slc_autoconnect_task);
        ESP_RETURN_ON_FALSE(task_ok == pdPASS, ESP_ERR_NO_MEM, TAG, "create HFP SLC reconnect task failed");
    }
    return ESP_OK;
}

esp_err_t bt_hfp_hf_connect_audio(void)
{
    if (!s_slc_connected || !s_has_peer_bda) {
        ESP_LOGW(TAG, "cannot connect HFP audio: SLC not connected");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_audio_connected) {
        return ESP_OK;
    }

    ESP_LOGI(TAG, "requesting HFP audio connection");
    return esp_hf_client_connect_audio(s_peer_bda);
}

esp_err_t bt_hfp_hf_disconnect_audio(void)
{
    if (!s_slc_connected || !s_has_peer_bda) {
        return ESP_ERR_INVALID_STATE;
    }

    if (!s_audio_connected) {
        return ESP_OK;
    }

    ESP_LOGI(TAG, "requesting HFP audio disconnection");
    return esp_hf_client_disconnect_audio(s_peer_bda);
}

bool bt_hfp_hf_is_slc_connected(void)
{
    return s_slc_connected;
}

bool bt_hfp_hf_is_audio_connected(void)
{
    return s_audio_connected;
}
