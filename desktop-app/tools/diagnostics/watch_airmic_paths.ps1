$ErrorActionPreference = "SilentlyContinue"

$script:ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:StatusProbe = Join-Path $script:ProjectRoot "tools\audio_probe\bin\AirMicAudioProbe_status.exe"
$script:LastSignature = ""
$script:TargetPort = if ($env:AIRMIC_COM_PORT) { $env:AIRMIC_COM_PORT } else { "COM10" }

function Write-Banner {
    Write-Host "================ AirMic Path Watch ================" -ForegroundColor Cyan
    Write-Host "Actions:"
    Write-Host "1. Plug or unplug the board."
    Write-Host "2. Connect or disconnect HFP if needed."
    Write-Host "3. Change Windows default microphone if needed."
    Write-Host "4. Paste the full log back here."
    Write-Host ("Target serial port: " + $script:TargetPort)
    Write-Host "Press Ctrl+C to stop."
    Write-Host "===================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Get-Timestamp {
    return (Get-Date).ToString("HH:mm:ss")
}

function Get-ProbeSnapshot {
    if (-not (Test-Path $script:StatusProbe)) {
        return [ordered]@{
            probe_ok = $false
            default_comm = "[status probe missing]"
            default_multi = "[status probe missing]"
            airmic_endpoints = @()
        }
    }

    $lines = & $script:StatusProbe --default-input --list --all 2>&1
    $defaultComm = ""
    $defaultMulti = ""
    $airmicEndpoints = New-Object System.Collections.ArrayList

    foreach ($line in $lines) {
        $text = [string]$line
        if ($text -match '^DEFAULT_INPUT \(Communications\): (.+)$') {
            $defaultComm = $Matches[1].Trim()
            continue
        }
        if ($text -match '^DEFAULT_INPUT \(Multimedia\): (.+)$') {
            $defaultMulti = $Matches[1].Trim()
            continue
        }
        if ($text -match '^\d+:\s+(.+?)\s+\[(Active|Disabled|NotPresent|Unplugged)\]$') {
            $friendly = $Matches[1].Trim()
            $state = $Matches[2].Trim()
            $lower = $friendly.ToLowerInvariant()
            if ($lower.Contains("esp32") -or $lower.Contains("airmic") -or $lower.Contains("hands-free") -or $lower.Contains("hands free")) {
                $null = $airmicEndpoints.Add(($friendly + " [" + $state + "]"))
            }
        }
    }

    if (-not $defaultComm) { $defaultComm = "[none]" }
    if (-not $defaultMulti) { $defaultMulti = "[none]" }

    return [ordered]@{
        probe_ok = $true
        default_comm = $defaultComm
        default_multi = $defaultMulti
        airmic_endpoints = @($airmicEndpoints)
    }
}

function Is-AirMicName {
    param(
        [string]$Text
    )

    if (-not $Text) {
        return $false
    }
    $lower = $Text.ToLowerInvariant()
    return (
        $lower.Contains("esp32-airmic-hfp") -or
        $lower.Contains("airmic-hfp") -or
        $lower.Contains("esp32-airmic") -or
        $lower.Contains("hands-free ag")
    )
}

function Get-PnpSnapshot {
    $devices = Get-PnpDevice |
        Where-Object {
            (Is-AirMicName -Text $_.FriendlyName) -or
            (Is-AirMicName -Text $_.InstanceId)
        } |
        Sort-Object Class, FriendlyName, InstanceId

    $rows = New-Object System.Collections.ArrayList
    foreach ($dev in $devices) {
        if ($dev.FriendlyName) {
            $name = $dev.FriendlyName
        } else {
            $name = $dev.InstanceId
        }
        $row = ($dev.Class + " ; " + $dev.Status + " ; " + $name)
        $null = $rows.Add($row)
    }
    return @($rows)
}

function Get-SerialSnapshot {
    $ports = Get-CimInstance Win32_SerialPort | Sort-Object DeviceID
    $rows = New-Object System.Collections.ArrayList
    foreach ($port in $ports) {
        $name = [string]$port.Name
        $pnpId = [string]$port.PNPDeviceID
        if (
            $name -match [regex]::Escape($script:TargetPort) -or
            $pnpId -match [regex]::Escape($script:TargetPort)
        ) {
            $row = ($port.DeviceID + " ; " + $port.Status + " ; " + $port.Name)
            $null = $rows.Add($row)
        }
    }
    return @($rows)
}

function Get-WmiPnpSnapshot {
    $devices = Get-CimInstance Win32_PnPEntity |
        Where-Object {
            (Is-AirMicName -Text $_.Name) -or
            (Is-AirMicName -Text $_.DeviceID) -or
            (
                [string]$_.PNPClass -eq 'Ports' -and
                (
                    [string]$_.Name -match [regex]::Escape($script:TargetPort)
                )
            )
        } |
        Sort-Object PNPClass, Name, DeviceID

    $rows = New-Object System.Collections.ArrayList
    foreach ($dev in $devices) {
        if ($null -eq $dev.Present) {
            $present = "?"
        } elseif ($dev.Present) {
            $present = "True"
        } else {
            $present = "False"
        }
        $row = ($dev.PNPClass + " ; " + $dev.Status + " ; Present=" + $present + " ; " + $dev.Name)
        $null = $rows.Add($row)
    }
    return @($rows)
}

function New-Signature {
    param(
        [hashtable]$Probe,
        [string[]]$Pnp,
        [string[]]$Serial,
        [string[]]$WmiPnp
    )

    $parts = @()
    $parts += ("COMM=" + $Probe.default_comm)
    $parts += ("MULTI=" + $Probe.default_multi)
    $parts += ("ENDPOINTS=" + [string]::Join(' || ', $Probe.airmic_endpoints))
    $parts += ("PNP=" + [string]::Join(' || ', $Pnp))
    $parts += ("SERIAL=" + [string]::Join(' || ', $Serial))
    $parts += ("WMIPNP=" + [string]::Join(' || ', $WmiPnp))
    return ($parts -join "`n")
}

function Print-Snapshot {
    param(
        [hashtable]$Probe,
        [string[]]$Pnp,
        [string[]]$Serial,
        [string[]]$WmiPnp
    )

    $ts = Get-Timestamp
    Write-Host ""
    Write-Host ("[" + $ts + "]`tstate_change") -ForegroundColor Green
    Write-Host ("[" + $ts + "]`tdefault_comm`t" + $Probe.default_comm)
    Write-Host ("[" + $ts + "]`tdefault_multi`t" + $Probe.default_multi)

    if ($Probe.airmic_endpoints.Count -gt 0) {
        foreach ($row in $Probe.airmic_endpoints) {
            Write-Host ("[" + $ts + "]`tairmic_endpoint`t" + $row)
        }
    } else {
        Write-Host ("[" + $ts + "]`tairmic_endpoint`t[none]")
    }

    if ($Pnp.Count -gt 0) {
        foreach ($row in $Pnp) {
            Write-Host ("[" + $ts + "]`tairmic_pnp`t" + $row)
        }
    } else {
        Write-Host ("[" + $ts + "]`tairmic_pnp`t[none]")
    }

    if ($Serial.Count -gt 0) {
        foreach ($row in $Serial) {
            Write-Host ("[" + $ts + "]`tairmic_serial`t" + $row)
        }
    } else {
        Write-Host ("[" + $ts + "]`tairmic_serial`t[none]")
    }

    if ($WmiPnp.Count -gt 0) {
        foreach ($row in $WmiPnp) {
            Write-Host ("[" + $ts + "]`tairmic_wmi_pnp`t" + $row)
        }
    } else {
        Write-Host ("[" + $ts + "]`tairmic_wmi_pnp`t[none]")
    }
}

Write-Banner

while ($true) {
    $probe = Get-ProbeSnapshot
    $pnp = Get-PnpSnapshot
    $serial = Get-SerialSnapshot
    $wmiPnp = Get-WmiPnpSnapshot
    $signature = New-Signature -Probe $probe -Pnp $pnp -Serial $serial -WmiPnp $wmiPnp

    if ($signature -ne $script:LastSignature) {
        Print-Snapshot -Probe $probe -Pnp $pnp -Serial $serial -WmiPnp $wmiPnp
        $script:LastSignature = $signature
    }

    Start-Sleep -Milliseconds 800
}
