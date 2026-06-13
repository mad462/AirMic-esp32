#include "button_ptt.h"

#include "board_config.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "button_ptt";

#define BUTTON_TASK_STACK       2048
#define BUTTON_TASK_PRIORITY    4
#define BUTTON_POLL_INTERVAL_MS 10
#define BUTTON_DEBOUNCE_MS      60

typedef struct {
    button_input_id_t input_id;
    gpio_num_t gpio;
    const char *label;
    bool stable_state;
    bool last_raw_state;
    TickType_t last_change_tick;
} button_state_t;

typedef struct {
    button_ptt_callback_t callback;
    void *user_ctx;
} button_ctx_t;

static button_ctx_t s_ctx;
static TaskHandle_t s_button_task;
static button_state_t s_buttons[] = {
    { .input_id = BUTTON_INPUT_PTT, .gpio = PTT_BUTTON_GPIO, .label = "PTT" },
    { .input_id = BUTTON_INPUT_TONE_A, .gpio = TONE_A_BUTTON_GPIO, .label = "Tone A" },
    { .input_id = BUTTON_INPUT_TONE_B, .gpio = TONE_B_BUTTON_GPIO, .label = "Tone B" },
    { .input_id = BUTTON_INPUT_TONE_C, .gpio = TONE_C_BUTTON_GPIO, .label = "Tone C" },
};

static bool read_pressed(gpio_num_t gpio)
{
    return gpio_get_level(gpio) == 0;
}

static void button_task(void *arg)
{
    (void)arg;

    for (size_t i = 0; i < sizeof(s_buttons) / sizeof(s_buttons[0]); ++i) {
        s_buttons[i].stable_state = read_pressed(s_buttons[i].gpio);
        s_buttons[i].last_raw_state = s_buttons[i].stable_state;
        s_buttons[i].last_change_tick = xTaskGetTickCount();
        ESP_LOGI(TAG, "%s GPIO%d initial state: %s",
                 s_buttons[i].label,
                 s_buttons[i].gpio,
                 s_buttons[i].stable_state ? "pressed" : "released");
    }

    while (true) {
        TickType_t now = xTaskGetTickCount();
        for (size_t i = 0; i < sizeof(s_buttons) / sizeof(s_buttons[0]); ++i) {
            bool raw_state = read_pressed(s_buttons[i].gpio);
            if (raw_state != s_buttons[i].last_raw_state) {
                s_buttons[i].last_raw_state = raw_state;
                s_buttons[i].last_change_tick = now;
            }

            if (raw_state != s_buttons[i].stable_state &&
                (now - s_buttons[i].last_change_tick) >= pdMS_TO_TICKS(BUTTON_DEBOUNCE_MS)) {
                s_buttons[i].stable_state = raw_state;
                ESP_LOGI(TAG, "%s GPIO%d %s",
                         s_buttons[i].label,
                         s_buttons[i].gpio,
                         s_buttons[i].stable_state ? "pressed" : "released");
                if (s_ctx.callback != NULL) {
                    s_ctx.callback(s_buttons[i].input_id, s_buttons[i].stable_state, s_ctx.user_ctx);
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(BUTTON_POLL_INTERVAL_MS));
    }
}

esp_err_t button_ptt_init(button_ptt_callback_t callback, void *user_ctx)
{
    ESP_RETURN_ON_FALSE(callback != NULL, ESP_ERR_INVALID_ARG, TAG, "callback is required");
    ESP_RETURN_ON_FALSE(s_button_task == NULL, ESP_ERR_INVALID_STATE, TAG, "button already initialized");

    uint64_t pin_mask = 0;
    for (size_t i = 0; i < sizeof(s_buttons) / sizeof(s_buttons[0]); ++i) {
        pin_mask |= (1ULL << s_buttons[i].gpio);
    }

    gpio_config_t io_conf = {
        .pin_bit_mask = pin_mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    esp_err_t ret = gpio_config(&io_conf);
    ESP_RETURN_ON_ERROR(ret, TAG, "gpio_config failed");

    s_ctx.callback = callback;
    s_ctx.user_ctx = user_ctx;

    BaseType_t task_ok = xTaskCreate(
        button_task,
        "button_ptt",
        BUTTON_TASK_STACK,
        NULL,
        BUTTON_TASK_PRIORITY,
        &s_button_task);
    ESP_RETURN_ON_FALSE(task_ok == pdPASS, ESP_ERR_NO_MEM, TAG, "failed to create button task");

    ESP_LOGI(TAG, "PTT button initialized on GPIO%d, active-low, debounce=%dms",
             PTT_BUTTON_GPIO, BUTTON_DEBOUNCE_MS);
    if (PTT_BUTTON_GPIO == 0) {
        ESP_LOGW(TAG, "GPIO0 is a boot strap pin: do not hold the button while powering on or resetting");
    }
    ESP_LOGI(TAG, "Tone A/B/C buttons: GPIO%d / GPIO%d / GPIO%d, active-low",
             TONE_A_BUTTON_GPIO, TONE_B_BUTTON_GPIO, TONE_C_BUTTON_GPIO);

    return ESP_OK;
}
