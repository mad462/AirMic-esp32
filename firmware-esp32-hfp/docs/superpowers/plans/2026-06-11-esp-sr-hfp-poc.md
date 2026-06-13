# ESP-SR HFP POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal ESP-SR proof of concept to the existing ESP32 HFP microphone path and verify whether NS/VAD improves noise performance without breaking HFP audio.

**Architecture:** Insert ESP-SR AFE processing at the current 16 kHz mono mic stage in `hfp_audio_source.c`, before the existing 16 kHz to 8 kHz path. Keep a runtime-safe fallback so the firmware still boots and streams raw audio if ESP-SR init fails or is disabled.

**Tech Stack:** ESP-IDF 5.4, Classic Bluetooth HFP, ESP-SR 1.9.x, INMP441 over I2S, FreeRTOS tasks/ring buffer.

---

### Task 1: Add ESP-SR dependency and wrapper boundary

**Files:**
- Create: `D:\FUCKIDF\AirMic esp32 hfp\main\idf_component.yml`
- Create: `D:\FUCKIDF\AirMic esp32 hfp\main\sr_afe_wrapper.c`
- Create: `D:\FUCKIDF\AirMic esp32 hfp\main\sr_afe_wrapper.h`
- Modify: `D:\FUCKIDF\AirMic esp32 hfp\main\CMakeLists.txt`

- [ ] Declare the `espressif/esp-sr` dependency with a version that still supports classic ESP32.
- [ ] Create a focused wrapper around AFE init/feed/fetch/deinit so the rest of the audio pipeline does not know ESP-SR details.
- [ ] Make the wrapper expose a bypass-safe API:
  - `sr_afe_wrapper_init()`
  - `sr_afe_wrapper_process_frame()`
  - `sr_afe_wrapper_reset()`
  - `sr_afe_wrapper_is_enabled()`
  - `sr_afe_wrapper_log_heap()`

### Task 2: Wire ESP-SR into the mic pipeline with graceful fallback

**Files:**
- Modify: `D:\FUCKIDF\AirMic esp32 hfp\main\hfp_audio_source.c`
- Modify: `D:\FUCKIDF\AirMic esp32 hfp\main\hfp_audio_source.h`

- [ ] Insert the wrapper at the 16 kHz mono stage, before 8 kHz downsampling.
- [ ] Keep `shift=11` and current tone injection logic unchanged.
- [ ] If wrapper init fails, log the reason and continue with the existing non-ESP-SR path.
- [ ] Reset AFE state when PTT closes or when recording mode changes.
- [ ] Add heap and mode logs around init/start/stop so runtime pressure is visible on serial.

### Task 3: Verify build and resource impact

**Files:**
- Modify: `D:\FUCKIDF\AirMic esp32 hfp\README.md`

- [ ] Run `idf.py build` with the actual ESP-IDF 5.4 toolchain.
- [ ] Run `idf.py size` and record flash/IRAM/DRAM usage after ESP-SR is linked.
- [ ] Update README with:
  - what this POC enables
  - that only `NS + VAD` are attempted first
  - known risk that classic ESP32 RAM headroom is tight
  - how to read the new heap/status logs
