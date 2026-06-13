# AirMic Workspace Reorg And PySide6 Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the AirMic code into a clean workspace with parallel firmware and desktop projects, archive legacy experiments, and bootstrap a new PySide6 desktop application around the existing HFP tone-monitoring stack.

**Architecture:** Move the active HFP firmware into a new unified `D:\FUCKIDF\AirMic` workspace, archive old AirMic projects in trimmed form, then scaffold a new `desktop-app` project that separates PySide6 UI, reusable core logic, and Windows integration services. Reuse the current Python tone/shortcut/device logic and C# audio probe where they already work, but stop carrying the old Tkinter/BLE UI structure forward.

**Tech Stack:** ESP-IDF 5.4 firmware, Python 3 desktop app, PySide6 UI, existing C# WASAPI probe, PowerShell filesystem migration, pytest/py_compile verification.

---

### Task 1: Create The New AirMic Workspace Layout

**Files:**
- Create: `D:\FUCKIDF\AirMic\`
- Create: `D:\FUCKIDF\AirMic\archive\legacy\`
- Create: `D:\FUCKIDF\AirMic\desktop-app\`
- Create: `D:\FUCKIDF\AirMic\shared\`
- Modify: none
- Test: directory existence checks

- [ ] **Step 1: Create the new workspace root and top-level folders**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\archive\legacy' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\desktop-app' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\shared' | Out-Null
```

- [ ] **Step 2: Verify the new workspace folders exist**

Run:

```powershell
Get-ChildItem -Force 'D:\FUCKIDF\AirMic'
```

Expected: shows `archive`, `desktop-app`, and `shared`.

- [ ] **Step 3: Commit**

No git commit in this environment because the current project directories are not git repositories.

### Task 2: Move The Active HFP Firmware Project Into The New Workspace

**Files:**
- Move: `D:\FUCKIDF\AirMic esp32 hfp`
- Create: `D:\FUCKIDF\AirMic\README.md`
- Test: firmware directory presence, rebuild path checks

- [ ] **Step 1: Move the active firmware project into the new workspace**

Run:

```powershell
Move-Item -LiteralPath 'D:\FUCKIDF\AirMic esp32 hfp' -Destination 'D:\FUCKIDF\AirMic\firmware-esp32-hfp'
```

- [ ] **Step 2: Write a top-level workspace README**

Create `D:\FUCKIDF\AirMic\README.md` with content:

```markdown
# AirMic Workspace

## Active Projects

- `firmware-esp32-hfp`: current ESP32 HFP microphone firmware
- `desktop-app`: current Windows desktop control app

## Supporting Folders

- `shared`: shared docs and protocol notes
- `archive\legacy`: trimmed historical AirMic projects
```

- [ ] **Step 3: Verify the firmware project now exists at the new path**

Run:

```powershell
Get-ChildItem -Force 'D:\FUCKIDF\AirMic\firmware-esp32-hfp'
```

Expected: shows `main`, `docs`, `test`, `tools`, `README.md`, `sdkconfig`, etc.

- [ ] **Step 4: Commit**

No git commit in this environment because the current project directories are not git repositories.

### Task 3: Trim And Archive The Legacy AirMic Projects

**Files:**
- Move: `D:\FUCKIDF\AirMic esp32`
- Move: `D:\FUCKIDF\AirMic esp32 hfp gattptt`
- Move: `D:\FUCKIDF\AirMic_esp32_hfp`
- Move: `D:\FUCKIDF\S3 AirMic Bridge`
- Modify: archived copies after trimming build/cache artifacts
- Test: archived directory presence and trimmed contents

- [ ] **Step 1: Create the target archive directories**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\archive\legacy\airmic-ble-ptt' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\archive\legacy\airmic-hfp-duplicate' | Out-Null
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\archive\legacy\s3-airmic-bridge' | Out-Null
```

- [ ] **Step 2: Move each old project into the archive**

Run:

```powershell
Move-Item -LiteralPath 'D:\FUCKIDF\AirMic esp32' -Destination 'D:\FUCKIDF\AirMic\archive\legacy\airmic-ble-ptt'
Move-Item -LiteralPath 'D:\FUCKIDF\AirMic esp32 hfp gattptt' -Destination 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed'
Move-Item -LiteralPath 'D:\FUCKIDF\AirMic_esp32_hfp' -Destination 'D:\FUCKIDF\AirMic\archive\legacy\airmic-hfp-duplicate'
Move-Item -LiteralPath 'D:\FUCKIDF\S3 AirMic Bridge' -Destination 'D:\FUCKIDF\AirMic\archive\legacy\s3-airmic-bridge'
```

- [ ] **Step 3: Remove generated folders and files from the archived projects**

Run:

```powershell
$roots = @(
  'D:\FUCKIDF\AirMic\archive\legacy\airmic-ble-ptt',
  'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed',
  'D:\FUCKIDF\AirMic\archive\legacy\airmic-hfp-duplicate',
  'D:\FUCKIDF\AirMic\archive\legacy\s3-airmic-bridge'
)

foreach ($root in $roots) {
  Get-ChildItem -Path $root -Recurse -Directory -Force |
    Where-Object { $_.Name -in @('build', '__pycache__', '.pytest_cache') } |
    Remove-Item -Recurse -Force

  Get-ChildItem -Path $root -Recurse -File -Force |
    Where-Object {
      $_.Extension -in @('.bin', '.elf', '.map', '.pyc', '.log', '.err', '.pid') -or
      $_.Name -like 'idf_py_*' -or
      $_.DirectoryName -like '*\recordings*'
    } |
    Remove-Item -Force
}
```

- [ ] **Step 4: Verify the archive keeps source/docs but not generated build trees**

Run:

```powershell
Get-ChildItem -Recurse -Depth 2 'D:\FUCKIDF\AirMic\archive\legacy' | Select-Object FullName
```

Expected: source and docs remain, `build` and cache directories are absent.

- [ ] **Step 5: Commit**

No git commit in this environment because the current project directories are not git repositories.

### Task 4: Scaffold The New PySide6 Desktop Application

**Files:**
- Create: `D:\FUCKIDF\AirMic\desktop-app\app\main.py`
- Create: `D:\FUCKIDF\AirMic\desktop-app\app\windows\main_window.py`
- Create: `D:\FUCKIDF\AirMic\desktop-app\app\windows\log_window.py`
- Create: `D:\FUCKIDF\AirMic\desktop-app\app\widgets\`
- Create: `D:\FUCKIDF\AirMic\desktop-app\app\styles\`
- Create: `D:\FUCKIDF\AirMic\desktop-app\core\...`
- Create: `D:\FUCKIDF\AirMic\desktop-app\services\...`
- Create: `D:\FUCKIDF\AirMic\desktop-app\tests\`
- Create: `D:\FUCKIDF\AirMic\desktop-app\requirements.txt`
- Create: `D:\FUCKIDF\AirMic\desktop-app\README.md`
- Test: `python -m py_compile`

- [ ] **Step 1: Create the new desktop project directory structure**

Run:

```powershell
$dirs = @(
  'D:\FUCKIDF\AirMic\desktop-app\app',
  'D:\FUCKIDF\AirMic\desktop-app\app\windows',
  'D:\FUCKIDF\AirMic\desktop-app\app\widgets',
  'D:\FUCKIDF\AirMic\desktop-app\app\styles',
  'D:\FUCKIDF\AirMic\desktop-app\core',
  'D:\FUCKIDF\AirMic\desktop-app\core\config',
  'D:\FUCKIDF\AirMic\desktop-app\core\models',
  'D:\FUCKIDF\AirMic\desktop-app\core\shortcut',
  'D:\FUCKIDF\AirMic\desktop-app\core\tone_decode',
  'D:\FUCKIDF\AirMic\desktop-app\core\audio_devices',
  'D:\FUCKIDF\AirMic\desktop-app\services',
  'D:\FUCKIDF\AirMic\desktop-app\assets',
  'D:\FUCKIDF\AirMic\desktop-app\tests'
)
$dirs | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }
```

- [ ] **Step 2: Add the initial requirements file**

Create `D:\FUCKIDF\AirMic\desktop-app\requirements.txt` with content:

```text
PySide6>=6.7
numpy
pyserial
sounddevice
bleak
```

- [ ] **Step 3: Add the initial desktop app README**

Create `D:\FUCKIDF\AirMic\desktop-app\README.md` with content:

```markdown
# AirMic Desktop App

PySide6 desktop application for:

- HFP tone monitoring
- shortcut mapping
- Windows audio device routing
- serial-based device tuning
- AirMic background listener control
```

- [ ] **Step 4: Add the minimal PySide6 launcher**

Create `D:\FUCKIDF\AirMic\desktop-app\app\main.py` with content:

```python
import sys

from PySide6.QtWidgets import QApplication

from app.windows.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Add the minimal main window**

Create `D:\FUCKIDF\AirMic\desktop-app\app\windows\main_window.py` with content:

```python
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AirMic Desktop")
        self.resize(1200, 760)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("AirMic Desktop - PySide6 bootstrap"))
        self.setCentralWidget(root)
```

- [ ] **Step 6: Add the minimal log window**

Create `D:\FUCKIDF\AirMic\desktop-app\app\windows\log_window.py` with content:

```python
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget


class LogWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AirMic Logs")
        self.resize(1100, 720)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.addWidget(QLabel("AirMic log window bootstrap"))
        self.setCentralWidget(root)
```

- [ ] **Step 7: Add the first service stubs**

Create `D:\FUCKIDF\AirMic\desktop-app\services\backend_coordinator.py` with content:

```python
from dataclasses import dataclass


@dataclass
class BackendStatus:
    state: str = "stopped"
    message: str = ""


class BackendCoordinator:
    def __init__(self) -> None:
        self.status = BackendStatus()

    def start(self) -> None:
        self.status = BackendStatus(state="starting", message="bootstrap")

    def stop(self) -> None:
        self.status = BackendStatus(state="stopped", message="")

    def restart(self) -> None:
        self.status = BackendStatus(state="restarting", message="bootstrap")
```

- [ ] **Step 8: Run a syntax-only verification on the new bootstrap files**

Run:

```powershell
python -m py_compile `
  'D:\FUCKIDF\AirMic\desktop-app\app\main.py' `
  'D:\FUCKIDF\AirMic\desktop-app\app\windows\main_window.py' `
  'D:\FUCKIDF\AirMic\desktop-app\app\windows\log_window.py' `
  'D:\FUCKIDF\AirMic\desktop-app\services\backend_coordinator.py'
```

Expected: no output, exit code 0.

- [ ] **Step 9: Commit**

No git commit in this environment because the current project directories are not git repositories.

### Task 5: Migrate The Existing Active Desktop Logic Into The New Project

**Files:**
- Copy and adapt from archived `pc_bridge`
- Copy and adapt from archived `pc_tone_lab`
- Copy and adapt `pc_audio_bridge_cs`
- Create: new modules under `core` and `services`
- Test: `python -m py_compile` on migrated modules

- [ ] **Step 1: Copy the current C# probe source into the new desktop app**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe' | Out-Null
Copy-Item -LiteralPath 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_audio_bridge_cs\AirMicAudioProbe.cs' -Destination 'D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe\AirMicAudioProbe.cs'
Copy-Item -LiteralPath 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_audio_bridge_cs\README.md' -Destination 'D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe\README.md'
Copy-Item -Recurse -Force 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_audio_bridge_cs\bin' 'D:\FUCKIDF\AirMic\desktop-app\tools\audio_probe\bin'
```

- [ ] **Step 2: Copy the current Python desktop logic into a staging area inside the new project**

Run:

```powershell
New-Item -ItemType Directory -Force -Path 'D:\FUCKIDF\AirMic\desktop-app\migration_staging' | Out-Null
Copy-Item -LiteralPath 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_bridge\airmic_ptt_bridge.py' -Destination 'D:\FUCKIDF\AirMic\desktop-app\migration_staging\airmic_ptt_bridge.py'
Copy-Item -LiteralPath 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_bridge\airmic_ptt_gui.py' -Destination 'D:\FUCKIDF\AirMic\desktop-app\migration_staging\airmic_ptt_gui.py'
Copy-Item -LiteralPath 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_bridge\README.md' -Destination 'D:\FUCKIDF\AirMic\desktop-app\migration_staging\pc_bridge_README.md'
```

- [ ] **Step 3: Copy the tone codec helpers for reuse**

Run:

```powershell
Copy-Item -LiteralPath 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_tone_lab\tone_codec.py' -Destination 'D:\FUCKIDF\AirMic\desktop-app\core\tone_decode\tone_codec.py'
Copy-Item -LiteralPath 'D:\FUCKIDF\AirMic\archive\legacy\airmic-gattptt-mixed\pc_tone_lab\test_tone_codec.py' -Destination 'D:\FUCKIDF\AirMic\desktop-app\tests\test_tone_codec.py'
```

- [ ] **Step 4: Add package markers so the new project is importable**

Run:

```powershell
$initFiles = @(
  'D:\FUCKIDF\AirMic\desktop-app\app\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\app\windows\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\core\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\core\tone_decode\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\core\shortcut\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\core\audio_devices\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\core\config\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\core\models\__init__.py',
  'D:\FUCKIDF\AirMic\desktop-app\services\__init__.py'
)
$initFiles | ForEach-Object { New-Item -ItemType File -Force -Path $_ | Out-Null }
```

- [ ] **Step 5: Verify the staging/migrated Python modules still parse**

Run:

```powershell
python -m py_compile `
  'D:\FUCKIDF\AirMic\desktop-app\migration_staging\airmic_ptt_bridge.py' `
  'D:\FUCKIDF\AirMic\desktop-app\migration_staging\airmic_ptt_gui.py' `
  'D:\FUCKIDF\AirMic\desktop-app\core\tone_decode\tone_codec.py' `
  'D:\FUCKIDF\AirMic\desktop-app\tests\test_tone_codec.py'
```

Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

No git commit in this environment because the current project directories are not git repositories.

### Task 6: Verify The New Workspace State

**Files:**
- Verify: `D:\FUCKIDF\AirMic\...`
- Test: directory checks and py_compile checks

- [ ] **Step 1: Verify the new top-level AirMic workspace layout**

Run:

```powershell
Get-ChildItem -Force 'D:\FUCKIDF\AirMic'
```

Expected: shows `firmware-esp32-hfp`, `desktop-app`, `shared`, `archive`.

- [ ] **Step 2: Verify the active firmware folder exists and the old path is gone**

Run:

```powershell
Test-Path 'D:\FUCKIDF\AirMic\firmware-esp32-hfp'
Test-Path 'D:\FUCKIDF\AirMic esp32 hfp'
```

Expected:

- first command returns `True`
- second command returns `False`

- [ ] **Step 3: Verify the desktop app bootstrap still parses**

Run:

```powershell
python -m py_compile `
  'D:\FUCKIDF\AirMic\desktop-app\app\main.py' `
  'D:\FUCKIDF\AirMic\desktop-app\app\windows\main_window.py' `
  'D:\FUCKIDF\AirMic\desktop-app\app\windows\log_window.py' `
  'D:\FUCKIDF\AirMic\desktop-app\services\backend_coordinator.py' `
  'D:\FUCKIDF\AirMic\desktop-app\migration_staging\airmic_ptt_bridge.py' `
  'D:\FUCKIDF\AirMic\desktop-app\migration_staging\airmic_ptt_gui.py'
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

No git commit in this environment because the current project directories are not git repositories.
