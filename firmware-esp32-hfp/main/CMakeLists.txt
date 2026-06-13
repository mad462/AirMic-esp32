idf_component_register(
    SRCS
        "main.c"
        "audio_math.c"
        "hfp_audio_source.c"
        "button_ptt.c"
        "bt_hfp_hf.c"
    INCLUDE_DIRS "."
    REQUIRES
        bt
        esp_ringbuf
        esp_driver_gpio
        esp_driver_i2s
        nvs_flash
)

target_link_libraries(${COMPONENT_LIB} INTERFACE m)
