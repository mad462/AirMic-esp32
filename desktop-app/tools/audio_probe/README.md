# AirMic Audio Probe

Minimal C# / NAudio probe for checking whether Windows exposes the AirMic HFP microphone through WASAPI.

This project avoids the missing .NET SDK on this machine by compiling with the .NET Framework `csc.exe` and NAudio 1.10.

## Build

```powershell
cd "D:\FUCKIDF\AirMic esp32 hfp gattptt"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $csc /nologo /platform:x64 /target:exe `
  /out:pc_audio_bridge_cs\bin\AirMicAudioProbe.exe `
  /reference:pc_audio_bridge_cs\packages\NAudio.1.10.0\lib\net35\NAudio.dll `
  pc_audio_bridge_cs\AirMicAudioProbe.cs
Copy-Item -Force pc_audio_bridge_cs\packages\NAudio.1.10.0\lib\net35\NAudio.dll pc_audio_bridge_cs\bin\NAudio.dll
```

## List Devices

Active capture endpoints:

```powershell
.\pc_audio_bridge_cs\bin\AirMicAudioProbe.exe --list
```

All capture endpoints:

```powershell
.\pc_audio_bridge_cs\bin\AirMicAudioProbe.exe --list --all
```

Current observation on this PC:

```text
ESP32-AirMic-HFP Hands-Free appears in WASAPI, but currently as NotPresent/Unplugged.
```

That means the Python WDM-KS failure was real, and C#/WASAPI can see the endpoint only when Windows marks the HFP microphone as active/present.

## Capture

```powershell
.\pc_audio_bridge_cs\bin\AirMicAudioProbe.exe --device 2 --seconds 20
```

Wait until a matching AirMic endpoint becomes Active, then capture:

```powershell
.\pc_audio_bridge_cs\bin\AirMicAudioProbe.exe --all --name ESP32-AirMic-HFP --wait-active --seconds 20
```

The probe prints RMS once per second and detects the same START/STOP dual-tone events used by `pc_tone_lab`.

## Next Check

1. Make sure `ESP32-AirMic-HFP` is connected in Windows Bluetooth.
2. Open Windows Recording panel and select/use `ESP32-AirMic-HFP Hands-Free`.
3. Run `--list --all` again.
4. If the endpoint becomes `Active`, run capture on that device index.
