# AirMic Workspace Reorg And Desktop Design

## Goal

Reorganize the AirMic projects into a clean workspace with two first-class active projects:

- ESP32 HFP firmware
- Windows desktop application

At the same time, retire the old BLE/PTT mixed experiments into a dedicated legacy archive, and replace the current Tkinter-based control surface with a PySide6 desktop application that better matches the real usage model.

This design intentionally separates:

- firmware work
- desktop UI work
- system-integration services
- archived experiments

The outcome should make future iteration easier, reduce confusion about which project is current, and provide a more usable day-to-day operator interface.

## Current Situation

AirMic-related code is currently spread across multiple top-level folders:

- `D:\FUCKIDF\AirMic esp32`
- `D:\FUCKIDF\AirMic esp32 hfp`
- `D:\FUCKIDF\AirMic esp32 hfp gattptt`
- `D:\FUCKIDF\AirMic_esp32_hfp`
- `D:\FUCKIDF\S3 AirMic Bridge`

The currently active production direction is the HFP microphone path in `AirMic esp32 hfp`.

The current Tkinter GUI and related probe/serial/tone code live under `AirMic esp32 hfp gattptt`, but that folder also contains old firmware branches and BLE/PTT-era code that is no longer the main path.

This creates four active problems:

1. There is no single obvious root for the AirMic product.
2. Old experiments still sit next to current code and look live.
3. The desktop app mixes UI, service logic, probe management, serial control, and legacy BLE assumptions in one place.
4. The Tkinter UI is too noisy for normal use and too cramped for future growth.

## Workspace Target

Create a new shared workspace root:

```text
D:\FUCKIDF\AirMic
├─ firmware-esp32-hfp
├─ desktop-app
├─ shared
└─ archive
   └─ legacy
```

### Active Projects

#### `firmware-esp32-hfp`

This becomes the only active firmware project.

Source:

- `D:\FUCKIDF\AirMic esp32 hfp`

This project keeps:

- current ESP32 Classic BT HFP firmware
- audio source pipeline
- HFP control/audio reconnect logic
- build/test/docs needed for active firmware work

#### `desktop-app`

This becomes the only active Windows desktop application project.

Primary sources come from:

- `D:\FUCKIDF\AirMic esp32 hfp gattptt\pc_bridge`
- `D:\FUCKIDF\AirMic esp32 hfp gattptt\pc_audio_bridge_cs`
- selected reusable tone-code/test pieces from `pc_tone_lab`

This project will be rebuilt around PySide6 and will no longer depend on the old BLE/PTT GUI structure.

#### `shared`

Initially optional and lightweight.

This is reserved for:

- protocol notes
- tone encoding notes
- sample configs
- cross-project docs that should not belong only to firmware or only to desktop

If there is no immediate shared content, the folder may start nearly empty with a README.

### Legacy Archive

Legacy projects will move under:

```text
D:\FUCKIDF\AirMic\archive\legacy
```

Planned archive entries:

- `airmic-ble-ptt` from `AirMic esp32`
- `airmic-gattptt-mixed` from `AirMic esp32 hfp gattptt`
- `airmic-hfp-duplicate` from `AirMic_esp32_hfp`
- `s3-airmic-bridge` from `S3 AirMic Bridge`

Archive mode is **trimmed archive**, not raw snapshot.

That means archive copies keep:

- source code
- docs
- configuration files worth preserving

And remove:

- `build/`
- `__pycache__/`
- generated firmware binaries
- generated logs
- temporary recordings
- cache folders like `.pytest_cache`

## Desktop Application Design

## Framework Choice

The new desktop application will use **PySide6**.

Reasons:

- much better window/layout system than Tkinter
- natural support for multi-window UI
- better foundation for status panels, settings panels, and device lists
- easier long-term separation of UI from backend services

Full browser-style hot reload is not a goal.

Instead, development should optimize for:

- fast application restart
- stable service boundaries
- smaller UI modules

## Desktop App Folder Structure

Target structure:

```text
desktop-app
├─ app
│  ├─ main.py
│  ├─ windows
│  ├─ widgets
│  └─ styles
├─ core
│  ├─ tone_decode
│  ├─ shortcut
│  ├─ audio_devices
│  ├─ config
│  └─ models
├─ services
│  ├─ backend_coordinator.py
│  ├─ probe_service.py
│  ├─ serial_service.py
│  ├─ audio_device_service.py
│  └─ shortcut_service.py
├─ assets
├─ tests
├─ requirements.txt
└─ README.md
```

### Responsibilities

#### `app`

Pure UI layer:

- main window
- log window
- shared widgets
- themes / styles

The UI should render state and emit user intents. It should not own low-level serial/probe/audio logic.

#### `core`

Reusable business logic without GUI coupling:

- tone event models
- mapping definitions
- preset definitions
- configuration schemas
- validation
- lightweight pure functions

#### `services`

System-integration layer:

- child-process probe launch/monitor
- serial transport
- Windows audio device enumeration/control
- shortcut injection
- service state machine

#### `tests`

Focused regression coverage for:

- tone parsing / event mapping
- shortcut behavior
- device selection rules
- config validation

## Main Window Design

The main window should be quiet and task-focused.

It should not expose full logs or low-level serial tuning by default.

### Main Window Sections

#### 1. Top Status Bar

Required items:

- device connection status
- backend listening service status
- locked AirMic microphone
- current system default input
- current system default output

Required actions:

- `Restart Backend Listening`
- `Refresh Devices`

Backend listening service states:

- stopped
- starting
- running
- restarting
- error

This status is important enough to be first-class because the desktop app is centered around the background listener actually being alive and bound to the right microphone.

#### 2. Voice Model Preset Area

This is a top-level mode selector, not buried inside per-tone mappings.

Initial presets:

- `Qwen App`
  - default action: `Right Alt`
- `WeChat Voice Input`
  - default action: `Left Ctrl + Left Win`
- `Custom API`
  - reserved for future integration

Changing preset updates recommended mappings and default behavior, while still allowing manual overrides.

#### 3. Key Mapping Area

The mapping model should no longer center on the old STOP tone.

Instead, define:

- `Start Tone`
- `Tone A`
- `Tone B`
- `Tone C`

Rules:

- `Start Tone` remains the primary voice-input trigger and does not change into an arbitrary event category.
- `Tone A/B/C` are expandable functional carriers for future commands.

Each mapping row supports:

- action type
  - shortcut
  - reserved API action
  - disabled
- target action
- trigger style

For the current phase, shortcut actions are the main real implementation path.

#### 4. Audio Device Area

Display:

- whether AirMic HFP input is available
- which input device the probe is actually bound to
- current system default input
- current system default communication input
- current system default output

Actions:

- set AirMic as default input
- set AirMic as default communication input
- set selected output as default output
- rebind probe to AirMic

### Important Constraint

The software cannot guarantee that every external application will always use AirMic 100% of the time.

Why:

- some applications follow system default input
- some follow system default communication input
- some remember a manually chosen device
- some manage audio routing internally

What the app can do reliably:

- find the AirMic HFP endpoint
- bind the probe to the AirMic HFP endpoint
- expose current routing state clearly
- provide one-click system default switching

That is the correct product boundary and should be reflected in the implementation.

#### 5. Secondary Actions Area

Required actions:

- open log window
- release all shortcuts
- open Windows Bluetooth settings
- open Windows recording panel

This keeps recovery/debug tools available without cluttering the primary operator flow.

## Log Window Design

The log window should be a separate top-level `QMainWindow`, not a small dialog.

It should support side-by-side use with the main window during tuning and diagnosis.

### Log Window Sections

#### 1. Status Cards

Show:

- backend listening state
- serial state
- HFP state
- probe state
- locked input device
- latest recognized tone event
- latest shortcut action

#### 2. Parameter Tuning Area

This area is tied to serial access and should be enabled only when serial is available.

Parameters:

- microphone gain
- sample shift
- noise gate
- tone gain
- record mode

Each parameter should support:

- slider
- numeric input

Actions:

- read device parameters
- apply to device
- save to device
- restore defaults

Serial-port selection belongs in this window because it is part of the device tuning/debug flow rather than normal daily use.

#### 3. Log Detail Area

Features:

- category filter
  - all
  - service
  - audio
  - shortcut
  - serial
  - HFP
  - error
- clear log
- copy selected lines
- max retained line count

This reduces noise and prevents the UI from degrading over long sessions.

## Backend Service Design

The new desktop app should not let UI code directly manage multiple worker threads/processes.

Instead it should define explicit service objects plus a single coordinator.

### Core Services

#### `ProbeService`

Responsibilities:

- launch `AirMicAudioProbe`
- monitor process health
- receive parsed tone detection output
- restart probe when needed

#### `SerialService`

Responsibilities:

- open/close serial port
- read device logs
- send configuration commands
- parse structured device status

#### `AudioDeviceService`

Responsibilities:

- enumerate Windows input/output devices
- find the AirMic HFP input endpoint
- set default input/output/communication devices
- expose actual probe binding target

#### `ShortcutService`

Responsibilities:

- map `Start Tone`, `Tone A`, `Tone B`, `Tone C` into actions
- send shortcuts
- manage hold/release/debounce/failsafe behavior

### `BackendCoordinator`

Responsibilities:

- own lifecycle of all services
- expose unified backend-listener status
- fan in service events and fan them out to UI
- coordinate preset switching
- centralize restart logic

The UI should talk primarily to this coordinator instead of talking to each low-level service directly.

## Device Selection Design

Current behavior already prefers AirMic by name, which works well in normal multi-microphone setups.

The next version should harden this by persisting the chosen Windows endpoint identity.

### Selection Order

1. previously stored endpoint ID, if still present
2. exact AirMic HFP name match
3. AirMic-related keyword match
4. explicit user selection

This allows stable rebinding even when the system contains many microphones.

## Migration Plan

Implementation should avoid a giant destructive rewrite.

### Phase 1: Workspace Reorganization

1. Create new root:
   - `D:\FUCKIDF\AirMic`
2. Move current active HFP firmware to:
   - `D:\FUCKIDF\AirMic\firmware-esp32-hfp`
3. Create:
   - `D:\FUCKIDF\AirMic\desktop-app`
4. Create:
   - `D:\FUCKIDF\AirMic\archive\legacy`
5. Move trimmed historical projects into legacy archive

### Phase 2: Desktop Project Bootstrap

1. Scaffold PySide6 desktop app structure
2. Move reusable Python tone/shortcut logic into `core` and `services`
3. Move the C# probe into a dedicated sub-area of the desktop project
4. Keep the existing Tkinter GUI intact in archive until the PySide6 replacement is functional

### Phase 3: Main Window

1. Build status bar
2. Build preset selector
3. Build tone mapping section
4. Build audio device section
5. Build secondary actions

### Phase 4: Log Window

1. Build status cards
2. Build serial/parameter panel
3. Build categorized log view

### Phase 5: Service Hardening

1. Introduce `BackendCoordinator`
2. Centralize restart and error handling
3. Add persistent audio endpoint binding
4. Add tests for mapping/device selection/config parsing

## Archive Rules

When moving projects to `archive\legacy`, remove:

- `build/`
- `.pytest_cache/`
- `__pycache__/`
- generated `.bin`, `.elf`, `.map`
- probe/log output files
- temporary recordings

Retain:

- source
- tests
- docs
- readmes
- pin maps
- useful configuration

## Risks And Mitigations

### Risk: Migration mixes current and legacy code again

Mitigation:

- move archive content physically out of active projects early
- do not keep “temporary current copies” of old GUI code inside the new desktop app

### Risk: PySide6 rewrite stalls because backend logic is still tangled

Mitigation:

- first extract service/core modules
- then rebuild UI on top
- do not port Tkinter layout code directly

### Risk: Windows audio control behavior differs across apps

Mitigation:

- expose real current state in UI
- support default input + communication input switching
- store probe binding target independently from system defaults

### Risk: Directory move breaks ad hoc scripts and paths

Mitigation:

- do migration in a controlled sequence
- update README and launcher entrypoints immediately after moves
- keep archive names descriptive so old references can still be found

## Recommendation

Proceed with:

- the new `D:\FUCKIDF\AirMic` workspace root
- `firmware-esp32-hfp` and `desktop-app` as parallel first-class projects
- trimmed `archive\legacy`
- PySide6 desktop app rebuild with a coordinator/service architecture

This is the cleanest path that matches the actual product direction while preserving the experimental history for reference.
