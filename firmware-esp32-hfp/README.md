# ESP32 AirMic HFP Microphone

This firmware turns a classic ESP32 board plus an INMP441 I2S microphone into a Windows HFP hands-free microphone.

The current build is intentionally simplified:

- Classic Bluetooth only
- HFP HF/client role for microphone audio
- PTT control carried by START/STOP tones inside the HFP audio stream
- No BLE bridge
- No ESP-SR experimental noise reduction path

The goal now is stability, lower memory pressure, and easier recovery after reboot.

## Hardware

INMP441:

| INMP441 | ESP32 |
| --- | --- |
| VDD | 3V3 |
| GND | GND |
| SCK | GPIO4 |
| WS | GPIO17 |
| SD | GPIO15 |
| L/R | GND |

Status LED:

| Signal | ESP32 |
| --- | --- |
| HFP status LED | GPIO2 |

Button:

| Button side | ESP32 |
| --- | --- |
| One side | GPIO13 |
| Other side | GND |

GPIO13 is used as the active-low PTT input in this build.

## Bluetooth Mode

This firmware uses:

- Classic Bluetooth only
- HFP HF/client role
- HCI SCO audio data path
- CVSD narrowband audio
- 8 kHz, 16-bit mono PCM sent to the HFP stack

The INMP441 is sampled at 16 kHz and then downsampled 2:1 to 8 kHz for CVSD.

## Build

Use ESP-IDF 5.x:

```powershell
cd "D:\FUCKIDF\AirMic esp32 hfp"
idf.py set-target esp32
idf.py build
```

This build no longer links `ESP-SR`. The audio path is the lean legacy chain: raw I2S capture, DC blocking, optional noise gate smoothing, gain, then 16 kHz to 8 kHz downsampling.

## Flash

```powershell
idf.py -p COM10 flash monitor
```

If auto-download fails, use the board's download/reset controls as needed for your specific dev board.

## Windows Pairing

1. Flash and reset the ESP32.
2. Open Windows Settings > Bluetooth & devices > Add device > Bluetooth.
3. Pair `ESP32-AirMic-HFP`.
4. Open Sound settings and look for the hands-free/headset input device.
5. Select that input device in a recorder or meeting app.

Windows HFP behavior varies by adapter and driver. If Windows pairs but does not open SCO audio, press GPIO13 once; the firmware will request an HFP audio connection when SLC is connected.

After the first successful pairing, the firmware stores the host Bluetooth address in NVS and will try to reconnect the HFP control link automatically after each reboot. This helps with normal restarts, but Windows can still refuse an automatic reconnect depending on the local Bluetooth stack, driver, or whether the device was removed from Windows.

## Current HFP Tone-Control Behavior

- In `mode record ptt`, the microphone gate opens only while the PTT button is held.
- In `mode record always`, the microphone stays open for debug capture.
- GPIO13 pressed: injects a START tone into the HFP microphone stream and requests HFP SCO audio if SLC is connected but audio is not connected yet.
- GPIO13 released: injects repeated STOP tones into the HFP microphone stream.
- GPIO2 LED slow blink: no HFP SLC connection.
- GPIO2 LED fast blink: SLC connected, but HFP audio is not connected.
- GPIO2 LED solid on: HFP audio is connected.

## Runtime Audio Config

The firmware accepts newline-terminated serial commands on COM10:

```text
cfg gain <q8>
cfg gate <pcm>
cfg tone <q8>
cfg shift <bits>
cfg show
cfg save
cfg reset
mode record ptt
mode record always
audio connect
audio disconnect
```

Values:

- `gain`: microphone gain in Q8 fixed point. `256 = 1.00x`, `512 = 2.00x`, `1024 = 4.00x`. Allowed range is `64..4096`.
- `gate`: noise gate threshold in PCM units. `0` disables the gate. Allowed range is `0..6000`.
- `tone`: embedded START/STOP tone gain in Q8 fixed point. `256 = 1.00x`. Allowed range is `64..768`.
- `shift`: right shift applied when converting INMP441 32-bit samples to 16-bit PCM. Smaller values are louder. Default is `11` in the current HFP experiments.
- `cfg save` writes the current audio settings to NVS so they survive reboot.
- `cfg reset` restores the compiled defaults in RAM. Run `cfg save` afterwards if you also want to overwrite the saved settings.
- `sr on`, `sr off`, `sr show` are still accepted as compatibility commands for the PC GUI, but the firmware no longer enables ESP-SR in this build.

The PC GUI exposes these settings under `ESP32 音频参数`. Use `应用到设备` to apply temporary runtime settings.

## Serial Logs To Watch

Expected startup:

```text
Classic BT HFP HF initialized name='ESP32-AirMic-HFP'
I2S mic initialized: 16000 Hz -> HFP CVSD 8000 Hz PCM
```

Expected after Windows connects:

```text
HFP connection state=slc_connected
HFP audio state=connected_cvsd
```

Expected while pressing GPIO13:

```text
PTT pressed, SLC=connected audio=connected
queued HFP tone START
mic peak=... rms=...
queued HFP tone STOP
```

## Known Limits

- This build uses CVSD narrowband audio, not mSBC wideband.
- Speaker playback from Windows is ignored; only incoming packet counts are logged.
- Windows may require selecting the hands-free input device before it opens SCO audio.
- Automatic reconnect restores the bonded HFP control link when Windows allows outbound reconnect from the ESP32 side, but it cannot force Windows to accept every reconnect attempt.
- Shortcut output is handled by the PC GUI after it decodes HFP START/STOP tones.
