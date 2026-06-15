using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Management;
using System.Threading;
using NAudio.CoreAudioApi;
using NAudio.CoreAudioApi.Interfaces;
using NAudio.Wave;

namespace AirMicAudioProbe
{
    internal static class Program
    {
        private const int TargetSampleRate = 8000;
        private const int SymbolMs = 150;
        private const int HopMs = 20;
        private const double StartRms = 0.008;
        private const double VadVoiceRms = 0.0028;
        private const int VadStartGraceMs = 220;
        private const int VadSilenceMs = 350;
        private const int VadNoSpeechStopMs = 700;
        private const double MinDetectScore = 5.0;
        private const double MinDetectToneShare = 0.06;
        private const double MinCandidateScore = 3.0;
        private const double MinCandidateToneShare = 0.04;
        private static readonly Dictionary<int, double> LowFreqs = new Dictionary<int, double> { { 0, 1200.0 }, { 1, 1450.0 } };
        private static readonly Dictionary<int, double> HighFreqs = new Dictionary<int, double> { { 0, 2300.0 }, { 1, 2750.0 } };

        private static readonly Dictionary<string, string> Events = new Dictionary<string, string>
        {
            { "0:0", "A" },
            { "0:1", "START" },
            { "1:0", "B" },
            { "1:1", "C" },
        };

        private static int Main(string[] args)
        {
            if (args.Contains("--help") || args.Contains("-h"))
            {
                PrintUsage();
                return 0;
            }

            bool includeAll = args.Contains("--all");
            bool waitActive = args.Contains("--wait-active");
            var devices = EnumerateCaptureDevices(includeAll);
            if (args.Contains("--default-input"))
            {
                PrintDefaultInput();
                if (!args.Contains("--list"))
                {
                    return 0;
                }
            }
            if (args.Contains("--list"))
            {
                PrintDevices(devices);
                return 0;
            }
            if (args.Contains("--watch-status"))
            {
                string watchNameFilter = GetStringArg(args, "--name", "ESP32-AirMic-HFP");
                return WatchStatus(watchNameFilter);
            }

            int deviceIndex = GetIntArg(args, "--device", PickAirMicIndex(devices, false));
            string nameFilter = GetStringArg(args, "--name", null);
            if (!string.IsNullOrEmpty(nameFilter))
            {
                int namedIndex = PickNameIndex(devices, nameFilter, false);
                if (namedIndex >= 0)
                {
                    deviceIndex = namedIndex;
                }
            }
            int seconds = GetIntArg(args, "--seconds", 0);
            int minAudioTimeoutMs = GetIntArg(args, "--min-audio-timeout-ms", 0);
            if (deviceIndex < 0 || deviceIndex >= devices.Count)
            {
                Console.WriteLine("No capture device selected. Use --list and --device N.");
                return 2;
            }

            if (waitActive)
            {
                deviceIndex = WaitForActiveDevice(nameFilter, deviceIndex);
                if (deviceIndex < 0)
                {
                    Console.WriteLine("No active matching capture endpoint found.");
                    return 4;
                }
                devices = EnumerateCaptureDevices(false);
            }

            try
            {
                return RunCapture(devices[deviceIndex], deviceIndex, seconds, minAudioTimeoutMs);
            }
            catch (Exception ex)
            {
                Console.WriteLine("Capture failed.");
                Console.WriteLine("Exception type: {0}", ex.GetType().FullName);
                Console.WriteLine("HResult: 0x{0:X8}", ex.HResult);
                try
                {
                    Console.WriteLine("Message: {0}", ex.Message);
                }
                catch
                {
                    Console.WriteLine("Message: <unprintable>");
                }
                return 3;
            }
        }

        private static void PrintUsage()
        {
            Console.WriteLine("AirMicAudioProbe.exe --list");
            Console.WriteLine("AirMicAudioProbe.exe --device N --seconds 20");
            Console.WriteLine("AirMicAudioProbe.exe --all --name ESP32-AirMic-HFP --wait-active --seconds 0 --min-audio-timeout-ms 4000");
            Console.WriteLine("AirMicAudioProbe.exe --watch-status --name ESP32-AirMic-HFP");
        }

        private static int GetIntArg(string[] args, string name, int fallback)
        {
            int value;
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == name && int.TryParse(args[i + 1], out value))
                {
                    return value;
                }
            }
            return fallback;
        }

        private static string GetStringArg(string[] args, string name, string fallback)
        {
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == name)
                {
                    return args[i + 1];
                }
            }
            return fallback;
        }

        private static List<MMDevice> EnumerateCaptureDevices(bool includeAll)
        {
            using (var enumerator = new MMDeviceEnumerator())
            {
                return enumerator.EnumerateAudioEndPoints(DataFlow.Capture, includeAll ? DeviceState.All : DeviceState.Active).ToList();
            }
        }

        private static void PrintDevices(List<MMDevice> devices)
        {
            for (int i = 0; i < devices.Count; i++)
            {
                var device = devices[i];
                Console.WriteLine("{0}: {1} [{2}]", i, device.FriendlyName, device.State);
                Console.WriteLine("   ID: {0}", device.ID);
            }
        }

        private static void PrintDefaultInput()
        {
            using (var enumerator = new MMDeviceEnumerator())
            {
                MMDevice communications = null;
                MMDevice multimedia = null;
                try
                {
                    communications = enumerator.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Communications);
                }
                catch
                {
                }
                try
                {
                    multimedia = enumerator.GetDefaultAudioEndpoint(DataFlow.Capture, Role.Multimedia);
                }
                catch
                {
                }

                if (communications != null)
                {
                    Console.WriteLine("DEFAULT_INPUT (Communications): {0} [{1}]", communications.FriendlyName, communications.State);
                }
                else
                {
                    Console.WriteLine("DEFAULT_INPUT (Communications): <none>");
                }

                if (multimedia != null)
                {
                    Console.WriteLine("DEFAULT_INPUT (Multimedia): {0} [{1}]", multimedia.FriendlyName, multimedia.State);
                }
                else
                {
                    Console.WriteLine("DEFAULT_INPUT (Multimedia): <none>");
                }
            }
        }

        private static int PickAirMicIndex(List<MMDevice> devices, bool requireActive)
        {
            int fallback = -1;
            for (int i = 0; i < devices.Count; i++)
            {
                string name = devices[i].FriendlyName.ToLowerInvariant();
                if (name.Contains("airmic") || name.Contains("esp32") || name.Contains("hands-free") || name.Contains("hands free"))
                {
                    if (!requireActive || devices[i].State == DeviceState.Active)
                    {
                        return i;
                    }
                    if (fallback < 0)
                    {
                        fallback = i;
                    }
                }
            }
            return requireActive ? -1 : (fallback >= 0 ? fallback : (devices.Count > 0 ? 0 : -1));
        }

        private static int PickNameIndex(List<MMDevice> devices, string filter, bool requireActive)
        {
            string loweredFilter = filter.ToLowerInvariant();
            int fallback = -1;
            for (int i = 0; i < devices.Count; i++)
            {
                string name = devices[i].FriendlyName.ToLowerInvariant();
                if (name.Contains(loweredFilter))
                {
                    if (!requireActive || devices[i].State == DeviceState.Active)
                    {
                        return i;
                    }
                    if (fallback < 0)
                    {
                        fallback = i;
                    }
                }
            }
            return requireActive ? -1 : fallback;
        }

        private static int WaitForActiveDevice(string nameFilter, int previousIndex)
        {
            Console.WriteLine("Waiting for matching WASAPI capture endpoint to become Active...");
            while (true)
            {
                var activeDevices = EnumerateCaptureDevices(false);
                int activeIndex;
                if (!string.IsNullOrEmpty(nameFilter))
                {
                    activeIndex = PickNameIndex(activeDevices, nameFilter, true);
                }
                else
                {
                    activeIndex = PickAirMicIndex(activeDevices, true);
                }

                if (activeIndex >= 0)
                {
                    Console.WriteLine("Active endpoint found: {0}: {1}", activeIndex, activeDevices[activeIndex].FriendlyName);
                    return activeIndex;
                }

                var allDevices = EnumerateCaptureDevices(true);
                int visibleIndex = !string.IsNullOrEmpty(nameFilter)
                    ? PickNameIndex(allDevices, nameFilter, false)
                    : PickAirMicIndex(allDevices, false);
                if (visibleIndex >= 0)
                {
                    Console.WriteLine("{0}: {1} [{2}]",
                        DateTime.Now.ToString("HH:mm:ss"),
                        allDevices[visibleIndex].FriendlyName,
                        allDevices[visibleIndex].State);
                }
                else if (previousIndex >= 0)
                {
                    Console.WriteLine("{0}: previous index {1}, no matching active endpoint yet",
                        DateTime.Now.ToString("HH:mm:ss"), previousIndex);
                }
                Thread.Sleep(1000);
            }
        }

        private static int RunCapture(MMDevice device, int deviceIndex, int seconds, int minAudioTimeoutMs)
        {
            Console.WriteLine("Using device {0}: {1} [{2}]", deviceIndex, device.FriendlyName, device.State);
            if (device.State != DeviceState.Active)
            {
                Console.WriteLine("Warning: selected endpoint is not Active. Capture may fail until Windows marks it present.");
            }
            WaveFormat mixFormat = null;
            try
            {
                mixFormat = device.AudioClient.MixFormat;
                Console.WriteLine("Device format: {0}", mixFormat);
            }
            catch (Exception ex)
            {
                Console.WriteLine("Could not read device mix format.");
                Console.WriteLine("Exception type: {0}", ex.GetType().FullName);
                Console.WriteLine("HResult: 0x{0:X8}", ex.HResult);
                try
                {
                    Console.WriteLine("Message: {0}", ex.Message);
                }
                catch
                {
                    Console.WriteLine("Message: <unprintable>");
                }
                throw;
            }

            var detector = new ToneDetector(TargetSampleRate, line => Console.WriteLine(line));
            var vad = new VadStopDetector(TargetSampleRate, line => Console.WriteLine(line));
            DateTime? stopAt = seconds > 0 ? DateTime.UtcNow.AddSeconds(seconds) : (DateTime?)null;
            long lastLevelLogTicks = 0;
            long firstAudioTicks = 0;
            long totalResampledSamples = 0;

            using (var capture = new WasapiCapture(device))
            {
                capture.DataAvailable += (sender, e) =>
                {
                    float[] samples = DecodeToMonoFloat(e.Buffer, e.BytesRecorded, capture.WaveFormat);
                    if (samples.Length == 0)
                    {
                        return;
                    }
                    if (Interlocked.Read(ref firstAudioTicks) == 0)
                    {
                        Interlocked.Exchange(ref firstAudioTicks, DateTime.UtcNow.Ticks);
                    }

                    float[] resampled = LinearResample(samples, capture.WaveFormat.SampleRate, TargetSampleRate);
                    long blockEndSample = totalResampledSamples + resampled.Length;
                    double rms = Math.Sqrt(resampled.Select(x => (double)x * x).DefaultIfEmpty(0.0).Average());
                    double peak = resampled.Select(x => Math.Abs((double)x)).DefaultIfEmpty(0.0).Max();
                    int nonZero = resampled.Count(x => Math.Abs(x) > 0.000001f);
                    long nowTicks = DateTime.UtcNow.Ticks;
                    if (nowTicks - lastLevelLogTicks > TimeSpan.FromSeconds(1).Ticks)
                    {
                        Console.WriteLine("RMS {0:F6} peak {1:F6} nonzero {2}/{3}", rms, peak, nonZero, resampled.Length);
                        lastLevelLogTicks = nowTicks;
                    }

                    foreach (var toneEvent in detector.Push(resampled))
                    {
                        Console.WriteLine("TONE {0} at {1:F3}s score={2:F1}", toneEvent.Name, toneEvent.TimeSeconds, toneEvent.Score);
                        if (toneEvent.Name == "START" || toneEvent.Name == "A" || toneEvent.Name == "B" || toneEvent.Name == "C")
                        {
                            vad.OnStart(toneEvent.Name, blockEndSample);
                        }
                    }

                    foreach (var vadEvent in vad.Observe(resampled, blockEndSample))
                    {
                        Console.WriteLine("VAD STOP at {0:F3}s silenceMs={1}", vadEvent.TimeSeconds, vadEvent.MetricMs);
                    }
                    totalResampledSamples = blockEndSample;
                };

                capture.RecordingStopped += (sender, e) =>
                {
                    if (e.Exception != null)
                    {
                        Console.WriteLine("Capture stopped with error: " + e.Exception.Message);
                    }
                };

                capture.StartRecording();
                Console.WriteLine("Capturing. Press Ctrl+C to stop.");
                DateTime captureStarted = DateTime.UtcNow;
                while (stopAt == null || DateTime.UtcNow < stopAt.Value)
                {
                    if (minAudioTimeoutMs > 0
                        && Interlocked.Read(ref firstAudioTicks) == 0
                        && (DateTime.UtcNow - captureStarted).TotalMilliseconds >= minAudioTimeoutMs)
                    {
                        Console.WriteLine("No audio callbacks within {0} ms; exiting for restart.", minAudioTimeoutMs);
                        break;
                    }
                    Thread.Sleep(100);
                }
                capture.StopRecording();
            }

            return 0;
        }

        private static int WatchStatus(string nameFilter)
        {
            using (var enumerator = new MMDeviceEnumerator())
            using (var watcher = new StatusWatcher(enumerator, nameFilter))
            {
                watcher.PrintSnapshot();
                enumerator.RegisterEndpointNotificationCallback(watcher);
                try
                {
                    while (true)
                    {
                        Thread.Sleep(250);
                    }
                }
                finally
                {
                    enumerator.UnregisterEndpointNotificationCallback(watcher);
                }
            }
        }

        private sealed class StatusWatcher : IMMNotificationClient, IDisposable
        {
            private readonly MMDeviceEnumerator enumerator;
            private readonly string loweredFilter;
            private readonly object sync = new object();
            private readonly List<ManagementEventWatcher> pnpWatchers = new List<ManagementEventWatcher>();
            private readonly Dictionary<string, string> relevantDeviceNamesById = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            private readonly HashSet<string> relevantDeviceTokens = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            private readonly CmNotifyCallback cmCallback;
            private IntPtr cmNotification = IntPtr.Zero;
            private static readonly Regex DeviceTokenRegex = new Regex(@"(?<![0-9A-F])[0-9A-F]{12}(?![0-9A-F])", RegexOptions.IgnoreCase | RegexOptions.Compiled);

            public StatusWatcher(MMDeviceEnumerator enumerator, string nameFilter)
            {
                this.enumerator = enumerator;
                loweredFilter = (nameFilter ?? string.Empty).ToLowerInvariant();
                cmCallback = OnCmNotification;
                RefreshRelevantDeviceIds();
                StartCmWatcher();
                StartPnpWatchers();
            }

            public void Dispose()
            {
                if (cmNotification != IntPtr.Zero)
                {
                    try
                    {
                        CmUnregisterNotification(cmNotification);
                    }
                    catch
                    {
                    }
                    cmNotification = IntPtr.Zero;
                }

                foreach (var watcher in pnpWatchers)
                {
                    try
                    {
                        watcher.Stop();
                    }
                    catch
                    {
                    }
                    watcher.Dispose();
                }
                pnpWatchers.Clear();
            }

            public void PrintSnapshot()
            {
                lock (sync)
                {
                    string defaultComm = GetDefaultInput(Role.Communications);
                    string defaultMulti = GetDefaultInput(Role.Multimedia);
                    string airmicName = string.Empty;
                    string airmicState = string.Empty;
                    bool devicePresent = false;
                    bool bluetoothConnected = IsAnyRelevantBluetoothDeviceConnected();
                    bool anyInput = false;

                    foreach (var device in enumerator.EnumerateAudioEndPoints(DataFlow.Capture, DeviceState.All))
                    {
                        if (device.State == DeviceState.Active)
                        {
                            anyInput = true;
                        }

                        string loweredName = (device.FriendlyName ?? string.Empty).ToLowerInvariant();
                        if (!loweredName.Contains(loweredFilter))
                        {
                            continue;
                        }

                        airmicName = device.FriendlyName ?? string.Empty;
                        airmicState = device.State.ToString();
                        devicePresent = true;
                        if (device.State == DeviceState.Active)
                        {
                            break;
                        }
                    }

                    Console.WriteLine(
                        "STATUS\tdefault_comm={0}\tdefault_multi={1}\tairmic={2}\tairmic_state={3}\tany_input={4}\tdevice_present={5}\tbt_connected={6}",
                        EscapeField(string.IsNullOrEmpty(defaultComm) ? "<none>" : defaultComm),
                        EscapeField(string.IsNullOrEmpty(defaultMulti) ? "<none>" : defaultMulti),
                        EscapeField(airmicName),
                        EscapeField(airmicState),
                        anyInput ? "true" : "false",
                        devicePresent ? "true" : "false",
                        bluetoothConnected ? "true" : "false");
                    Console.Out.Flush();
                }
            }

            private bool IsAnyRelevantBluetoothDeviceConnected()
            {
                var search = new BLUETOOTH_DEVICE_SEARCH_PARAMS();
                search.dwSize = Marshal.SizeOf(typeof(BLUETOOTH_DEVICE_SEARCH_PARAMS));
                search.fReturnAuthenticated = true;
                search.fReturnRemembered = true;
                search.fReturnUnknown = true;
                search.fReturnConnected = true;
                search.fIssueInquiry = false;
                search.cTimeoutMultiplier = 0;
                search.hRadio = IntPtr.Zero;

                var info = new BLUETOOTH_DEVICE_INFO();
                info.dwSize = Marshal.SizeOf(typeof(BLUETOOTH_DEVICE_INFO));

                IntPtr handle = BluetoothFindFirstDevice(ref search, ref info);
                if (handle == IntPtr.Zero)
                {
                    return false;
                }

                try
                {
                    do
                    {
                        string name = (info.szName ?? string.Empty).TrimEnd('\0').Trim();
                        string address = FormatBluetoothAddress(info.Address);
                        if (IsRelevantBluetoothDevice(name, address) && info.fConnected)
                        {
                            return true;
                        }

                        info = new BLUETOOTH_DEVICE_INFO();
                        info.dwSize = Marshal.SizeOf(typeof(BLUETOOTH_DEVICE_INFO));
                    }
                    while (BluetoothFindNextDevice(handle, ref info));
                }
                finally
                {
                    BluetoothFindDeviceClose(handle);
                }

                return false;
            }

            private bool IsRelevantBluetoothDevice(string name, string address)
            {
                string loweredName = (name ?? string.Empty).ToLowerInvariant();
                string normalizedAddress = (address ?? string.Empty).Replace(":", string.Empty).ToUpperInvariant();

                if (loweredName.Contains("esp32-airmic-hfp") || loweredName.Contains("airmic"))
                {
                    return true;
                }

                lock (sync)
                {
                    if (relevantDeviceTokens.Contains(normalizedAddress))
                    {
                        return true;
                    }
                }

                return false;
            }

            private static string FormatBluetoothAddress(ulong address)
            {
                return string.Format(
                    "{0:X2}{1:X2}{2:X2}{3:X2}{4:X2}{5:X2}",
                    (address >> 40) & 0xFF,
                    (address >> 32) & 0xFF,
                    (address >> 24) & 0xFF,
                    (address >> 16) & 0xFF,
                    (address >> 8) & 0xFF,
                    address & 0xFF);
            }

            private void EmitHint(string kind, string operation, string name)
            {
                Console.WriteLine(
                    "HINT\tkind={0}\toperation={1}\tname={2}",
                    EscapeField(kind),
                    EscapeField(operation),
                    EscapeField(name));
                Console.Out.Flush();
            }

            private void StartCmWatcher()
            {
                try
                {
                    var filter = new CmNotifyFilter();
                    filter.cbSize = Marshal.SizeOf(typeof(CmNotifyFilter));
                    filter.Flags = CmNotifyFilterFlagAllDeviceInstances;
                    filter.FilterType = CmNotifyFilterTypeDeviceInstance;
                    filter.Reserved = 0;
                    filter.DeviceInstanceId = string.Empty;

                    int result = CmRegisterNotification(ref filter, IntPtr.Zero, cmCallback, out cmNotification);
                    if (result != CrSuccess)
                    {
                        Console.WriteLine("WATCHER\tcm_register_failed\t{0}", result);
                        Console.Out.Flush();
                        cmNotification = IntPtr.Zero;
                    }
                }
                catch (Exception ex)
                {
                    Console.WriteLine("WATCHER\tcm_register_failed\t{0}", ex.Message);
                    Console.Out.Flush();
                }
            }

            private int OnCmNotification(IntPtr notify, IntPtr context, CmNotifyAction action, IntPtr eventData, int eventDataSize)
            {
                try
                {
                    if (eventData == IntPtr.Zero || eventDataSize < 8)
                    {
                        return ErrorSuccess;
                    }

                    int filterType = Marshal.ReadInt32(eventData, 0);
                    if (filterType != CmNotifyFilterTypeDeviceInstance)
                    {
                        return ErrorSuccess;
                    }

                    string instanceId = Marshal.PtrToStringUni(IntPtr.Add(eventData, 8)) ?? string.Empty;
                    if (string.IsNullOrWhiteSpace(instanceId))
                    {
                        return ErrorSuccess;
                    }

                    string resolvedName;
                    if (!TryResolveRelevantDeviceName(instanceId, out resolvedName))
                    {
                        return ErrorSuccess;
                    }

                    string operation = MapCmActionToOperation(action);
                    if (!string.IsNullOrEmpty(operation))
                    {
                        EmitHint("pnp", operation, resolvedName);
                    }

                    if (action == CmNotifyAction.DeviceInstanceEnumerated || action == CmNotifyAction.DeviceInstanceStarted)
                    {
                        RefreshRelevantDeviceIds();
                    }

                    PrintSnapshot();
                }
                catch (Exception ex)
                {
                    Console.WriteLine("WATCHER\tcm_event_failed\t{0}", ex.Message);
                    Console.Out.Flush();
                }
                return ErrorSuccess;
            }

            private static string MapCmActionToOperation(CmNotifyAction action)
            {
                switch (action)
                {
                    case CmNotifyAction.DeviceInstanceRemoved:
                        return "delete";
                    case CmNotifyAction.DeviceInstanceStarted:
                        return "start";
                    case CmNotifyAction.DeviceInstanceEnumerated:
                        return "create";
                    default:
                        return string.Empty;
                }
            }

            private bool TryResolveRelevantDeviceName(string instanceId, out string resolvedName)
            {
                lock (sync)
                {
                    if (relevantDeviceNamesById.TryGetValue(instanceId, out resolvedName))
                    {
                        return true;
                    }

                    foreach (var token in ExtractTokens(instanceId))
                    {
                        if (!relevantDeviceTokens.Contains(token))
                        {
                            continue;
                        }

                        resolvedName = "ESP32-AirMic-HFP";
                        return true;
                    }
                }

                resolvedName = string.Empty;
                return false;
            }

            public void OnDeviceStateChanged(string deviceId, DeviceState newState)
            {
                PrintSnapshot();
            }

            public void OnDeviceAdded(string pwstrDeviceId)
            {
                PrintSnapshot();
            }

            public void OnDeviceRemoved(string deviceId)
            {
                PrintSnapshot();
            }

            public void OnDefaultDeviceChanged(DataFlow flow, Role role, string defaultDeviceId)
            {
                if (flow == DataFlow.Capture)
                {
                    PrintSnapshot();
                }
            }

            public void OnPropertyValueChanged(string pwstrDeviceId, PropertyKey key)
            {
                PrintSnapshot();
            }

            private void StartPnpWatchers()
            {
                TryAddPnpWatcher("SELECT * FROM __InstanceDeletionEvent WITHIN 1 WHERE TargetInstance ISA 'Win32_PnPEntity'", "delete");
                TryAddPnpWatcher("SELECT * FROM __InstanceCreationEvent WITHIN 1 WHERE TargetInstance ISA 'Win32_PnPEntity'", "create");
                TryAddPnpWatcher("SELECT * FROM __InstanceModificationEvent WITHIN 1 WHERE TargetInstance ISA 'Win32_PnPEntity'", "modify");
            }

            private void TryAddPnpWatcher(string queryText, string operation)
            {
                try
                {
                    var query = new WqlEventQuery(queryText);
                    var watcher = new ManagementEventWatcher(query);
                    watcher.EventArrived += (sender, args) => OnPnpEvent(args, operation);
                    watcher.Start();
                    pnpWatchers.Add(watcher);
                }
                catch (Exception ex)
                {
                    Console.WriteLine("WATCHER\tpnp_{0}_failed\t{1}", operation, ex.Message);
                    Console.Out.Flush();
                }
            }

            private void OnPnpEvent(EventArrivedEventArgs args, string operation)
            {
                try
                {
                    var target = args.NewEvent["TargetInstance"] as ManagementBaseObject;
                    if (target == null)
                    {
                        return;
                    }

                    string name = Convert.ToString(target["Name"]) ?? string.Empty;
                    string deviceId = Convert.ToString(target["DeviceID"]) ?? string.Empty;
                    string pnpClass = Convert.ToString(target["PNPClass"]) ?? string.Empty;
                    if (!IsRelevantPnpEntity(name, deviceId, pnpClass))
                    {
                        return;
                    }

                    EmitHint("pnp", operation, name);
                    if (operation == "create" || operation == "modify")
                    {
                        RefreshRelevantDeviceIds();
                    }
                    PrintSnapshot();
                }
                catch (Exception ex)
                {
                    Console.WriteLine("WATCHER\tpnp_event_failed\t{0}", ex.Message);
                    Console.Out.Flush();
                }
            }

            private bool IsRelevantPnpEntity(string name, string deviceId, string pnpClass)
            {
                string loweredName = (name ?? string.Empty).ToLowerInvariant();
                string loweredDeviceId = (deviceId ?? string.Empty).ToLowerInvariant();
                string loweredClass = (pnpClass ?? string.Empty).ToLowerInvariant();

                bool nameMatch =
                    loweredName.Contains("airmic") ||
                    loweredName.Contains("esp32") ||
                    loweredName.Contains("hands-free") ||
                    loweredName.Contains("hands free");
                bool idMatch =
                    loweredDeviceId.Contains("airmic") ||
                    loweredDeviceId.Contains("esp32") ||
                    loweredDeviceId.Contains("bthenum");
                bool classMatch =
                    loweredClass.Contains("audioendpoint") ||
                    loweredClass.Contains("bluetooth") ||
                    loweredClass.Contains("media");

                return (nameMatch || idMatch) && classMatch;
            }

            private void RefreshRelevantDeviceIds()
            {
                var refreshed = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                var refreshedTokens = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                try
                {
                    using (var searcher = new ManagementObjectSearcher(
                        "SELECT Name, DeviceID, PNPClass FROM Win32_PnPEntity WHERE Name LIKE '%AirMic%' OR Name LIKE '%ESP32%' OR DeviceID LIKE 'BTHENUM\\\\DEV_%' OR DeviceID LIKE 'BTHHFENUM\\\\BTHHFPAUDIO%'"))
                    using (var results = searcher.Get())
                    {
                        foreach (ManagementObject device in results)
                        {
                            string name = Convert.ToString(device["Name"]) ?? string.Empty;
                            string deviceId = Convert.ToString(device["DeviceID"]) ?? string.Empty;
                            string pnpClass = Convert.ToString(device["PNPClass"]) ?? string.Empty;
                            if (!IsRelevantPnpEntity(name, deviceId, pnpClass))
                            {
                                continue;
                            }

                            if (!string.IsNullOrWhiteSpace(deviceId))
                            {
                                refreshed[deviceId] = string.IsNullOrWhiteSpace(name) ? "ESP32-AirMic-HFP" : name.Trim();
                            }

                            foreach (var token in ExtractTokens(deviceId))
                            {
                                refreshedTokens.Add(token);
                            }
                        }
                    }
                }
                catch
                {
                }

                lock (sync)
                {
                    relevantDeviceNamesById.Clear();
                    foreach (var pair in refreshed)
                    {
                        relevantDeviceNamesById[pair.Key] = pair.Value;
                    }

                    relevantDeviceTokens.Clear();
                    foreach (var token in refreshedTokens)
                    {
                        relevantDeviceTokens.Add(token);
                    }
                }
            }

            private static IEnumerable<string> ExtractTokens(string deviceId)
            {
                if (string.IsNullOrWhiteSpace(deviceId))
                {
                    yield break;
                }

                foreach (Match match in DeviceTokenRegex.Matches(deviceId.ToUpperInvariant()))
                {
                    yield return match.Value;
                }
            }

            private string GetDefaultInput(Role role)
            {
                try
                {
                    using (var device = enumerator.GetDefaultAudioEndpoint(DataFlow.Capture, role))
                    {
                        return device != null ? (device.FriendlyName ?? string.Empty) : string.Empty;
                    }
                }
                catch
                {
                    return string.Empty;
                }
            }

            private static string EscapeField(string value)
            {
                return (value ?? string.Empty).Replace("\\", "\\\\").Replace("\t", "\\t");
            }
        }

        private const int CrSuccess = 0;
        private const int ErrorSuccess = 0;
        private const int CmNotifyFilterTypeDeviceInstance = 2;
        private const int CmNotifyFilterFlagAllDeviceInstances = 0x00000002;

        private enum CmNotifyAction
        {
            DeviceInterfaceArrival = 0,
            DeviceInterfaceRemoval = 1,
            DeviceQueryRemove = 2,
            DeviceQueryRemoveFailed = 3,
            DeviceRemovePending = 4,
            DeviceRemoveComplete = 5,
            DeviceCustomEvent = 6,
            DeviceInstanceEnumerated = 7,
            DeviceInstanceStarted = 8,
            DeviceInstanceRemoved = 9,
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct CmNotifyFilter
        {
            public int cbSize;
            public int Flags;
            public int FilterType;
            public int Reserved;

            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 200)]
            public string DeviceInstanceId;
        }

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int CmNotifyCallback(
            IntPtr notify,
            IntPtr context,
            CmNotifyAction action,
            IntPtr eventData,
            int eventDataSize);

        [DllImport("CfgMgr32.dll", EntryPoint = "CM_Register_Notification", CharSet = CharSet.Unicode)]
        private static extern int CmRegisterNotification(
            ref CmNotifyFilter filter,
            IntPtr context,
            CmNotifyCallback callback,
            out IntPtr notifyContext);

        [DllImport("CfgMgr32.dll", EntryPoint = "CM_Unregister_Notification")]
        private static extern int CmUnregisterNotification(IntPtr notifyContext);

        [StructLayout(LayoutKind.Sequential)]
        private struct BLUETOOTH_DEVICE_SEARCH_PARAMS
        {
            public int dwSize;
            [MarshalAs(UnmanagedType.Bool)] public bool fReturnAuthenticated;
            [MarshalAs(UnmanagedType.Bool)] public bool fReturnRemembered;
            [MarshalAs(UnmanagedType.Bool)] public bool fReturnUnknown;
            [MarshalAs(UnmanagedType.Bool)] public bool fReturnConnected;
            [MarshalAs(UnmanagedType.Bool)] public bool fIssueInquiry;
            public byte cTimeoutMultiplier;
            public IntPtr hRadio;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct BLUETOOTH_DEVICE_INFO
        {
            public int dwSize;
            public ulong Address;
            public uint ulClassofDevice;
            [MarshalAs(UnmanagedType.Bool)] public bool fConnected;
            [MarshalAs(UnmanagedType.Bool)] public bool fRemembered;
            [MarshalAs(UnmanagedType.Bool)] public bool fAuthenticated;
            public SYSTEMTIME stLastSeen;
            public SYSTEMTIME stLastUsed;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 248)] public string szName;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SYSTEMTIME
        {
            public ushort wYear;
            public ushort wMonth;
            public ushort wDayOfWeek;
            public ushort wDay;
            public ushort wHour;
            public ushort wMinute;
            public ushort wSecond;
            public ushort wMilliseconds;
        }

        [DllImport("Bthprops.cpl", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr BluetoothFindFirstDevice(ref BLUETOOTH_DEVICE_SEARCH_PARAMS searchParams, ref BLUETOOTH_DEVICE_INFO deviceInfo);

        [DllImport("Bthprops.cpl", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool BluetoothFindNextDevice(IntPtr handle, ref BLUETOOTH_DEVICE_INFO deviceInfo);

        [DllImport("Bthprops.cpl", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool BluetoothFindDeviceClose(IntPtr handle);

        private static float[] DecodeToMonoFloat(byte[] buffer, int bytesRecorded, WaveFormat format)
        {
            int channels = Math.Max(1, format.Channels);
            var samples = new List<float>(bytesRecorded / Math.Max(1, format.BlockAlign));

            if (format.Encoding == WaveFormatEncoding.IeeeFloat && format.BitsPerSample == 32)
            {
                int frames = bytesRecorded / (4 * channels);
                for (int frame = 0; frame < frames; frame++)
                {
                    double sum = 0.0;
                    for (int ch = 0; ch < channels; ch++)
                    {
                        sum += BitConverter.ToSingle(buffer, (frame * channels + ch) * 4);
                    }
                    samples.Add((float)(sum / channels));
                }
                return samples.ToArray();
            }

            if (format.Encoding == WaveFormatEncoding.Pcm && format.BitsPerSample == 16)
            {
                int frames = bytesRecorded / (2 * channels);
                for (int frame = 0; frame < frames; frame++)
                {
                    double sum = 0.0;
                    for (int ch = 0; ch < channels; ch++)
                    {
                        short value = BitConverter.ToInt16(buffer, (frame * channels + ch) * 2);
                        sum += value / 32768.0;
                    }
                    samples.Add((float)(sum / channels));
                }
                return samples.ToArray();
            }

            return Array.Empty<float>();
        }

        private static float[] LinearResample(float[] input, int inputRate, int outputRate)
        {
            if (inputRate == outputRate)
            {
                return input;
            }
            if (input.Length == 0)
            {
                return Array.Empty<float>();
            }

            int outLength = Math.Max(1, (int)Math.Round(input.Length * (double)outputRate / inputRate));
            float[] output = new float[outLength];
            for (int i = 0; i < outLength; i++)
            {
                double src = i * (double)inputRate / outputRate;
                int idx = (int)Math.Floor(src);
                double frac = src - idx;
                float a = input[Math.Min(idx, input.Length - 1)];
                float b = input[Math.Min(idx + 1, input.Length - 1)];
                output[i] = (float)(a + (b - a) * frac);
            }
            return output;
        }

        private sealed class ToneDetector
        {
            private readonly int sampleRate;
            private readonly int frame;
            private readonly int hop;
            private readonly Action<string> logLine;
            private readonly List<float> buffer = new List<float>();
            private long processedSamples;
            private long suppressUntil;
            private long candidateSuppressUntil;

            public ToneDetector(int sampleRate, Action<string> logLine)
            {
                this.sampleRate = sampleRate;
                this.logLine = logLine;
                frame = sampleRate * SymbolMs / 1000;
                hop = sampleRate * HopMs / 1000;
            }

            public IEnumerable<ToneEvent> Push(float[] samples)
            {
                buffer.AddRange(samples);
                while (buffer.Count >= frame)
                {
                    if (processedSamples >= suppressUntil)
                    {
                        float[] chunk = buffer.Take(frame).ToArray();
                        ToneEvent detected = Detect(chunk, processedSamples);
                        if (detected != null)
                        {
                            suppressUntil = processedSamples + sampleRate * 350 / 1000;
                            yield return detected;
                        }
                    }

                    buffer.RemoveRange(0, Math.Min(hop, buffer.Count));
                    processedSamples += hop;
                }
            }

            private ToneEvent Detect(float[] chunk, long absoluteStart)
            {
                double[] windowed = new double[chunk.Length];
                double sumSquares = 0.0;
                for (int i = 0; i < chunk.Length; i++)
                {
                    double w = 0.5 - 0.5 * Math.Cos(2.0 * Math.PI * i / (chunk.Length - 1));
                    windowed[i] = chunk[i] * w;
                    sumSquares += windowed[i] * windowed[i];
                }

                double rms = Math.Sqrt(sumSquares / Math.Max(1, chunk.Length));
                if (rms < StartRms)
                {
                    return null;
                }

                var low = BestBit(windowed, LowFreqs);
                var high = BestBit(windowed, HighFreqs);
                double lowRatio = low.Power / Math.Max(low.SecondPower, 1e-12);
                double highRatio = high.Power / Math.Max(high.SecondPower, 1e-12);
                double score = Math.Min(lowRatio, highRatio);
                double toneShare = (low.Power + high.Power) / Math.Max(sumSquares * chunk.Length, 1e-12);

                string name;
                if (!Events.TryGetValue(low.Bit.ToString() + ":" + high.Bit.ToString(), out name))
                {
                    return null;
                }
                if (score < MinDetectScore || toneShare < MinDetectToneShare)
                {
                    if (absoluteStart >= candidateSuppressUntil &&
                        score >= MinCandidateScore &&
                        toneShare >= MinCandidateToneShare)
                    {
                        candidateSuppressUntil = absoluteStart + sampleRate * 300 / 1000;
                        logLine(string.Format(
                            "CANDIDATE {0} at {1:F3}s score={2:F1} toneShare={3:F3} rms={4:F4}",
                            name,
                            absoluteStart / (double)sampleRate,
                            score,
                            toneShare,
                            rms));
                    }
                    return null;
                }

                return new ToneEvent(name, absoluteStart / (double)sampleRate, score);
            }

            private BestResult BestBit(double[] samples, Dictionary<int, double> freqs)
            {
                var ranked = freqs
                    .Select(item => new PowerResult(item.Key, GoertzelPower(samples, item.Value)))
                    .OrderByDescending(item => item.Power)
                    .ToList();
                return new BestResult(ranked[0].Bit, ranked[0].Power, ranked.Count > 1 ? ranked[1].Power : 1e-12);
            }

            private double GoertzelPower(double[] samples, double freq)
            {
                int k = (int)(0.5 + samples.Length * freq / sampleRate);
                double omega = 2.0 * Math.PI * k / samples.Length;
                double coeff = 2.0 * Math.Cos(omega);
                double q0 = 0.0;
                double q1 = 0.0;
                double q2 = 0.0;
                foreach (double sample in samples)
                {
                    q0 = coeff * q1 - q2 + sample;
                    q2 = q1;
                    q1 = q0;
                }
                return q1 * q1 + q2 * q2 - coeff * q1 * q2;
            }
        }

        private sealed class VadStopDetector
        {
            private readonly int sampleRate;
            private readonly Action<string> logLine;
            private bool active;
            private bool voiceSeen;
            private string activeToneName = string.Empty;
            private long startSample;
            private long graceUntilSample;
            private long lastVoiceSample;

            public VadStopDetector(int sampleRate, Action<string> logLine)
            {
                this.sampleRate = sampleRate;
                this.logLine = logLine;
            }

            public void OnStart(string toneName, long absoluteSample)
            {
                string nextToneName = toneName ?? string.Empty;
                if (active && string.Equals(activeToneName, nextToneName, StringComparison.Ordinal))
                {
                    if (!voiceSeen)
                    {
                        startSample = absoluteSample;
                        graceUntilSample = absoluteSample + sampleRate * VadStartGraceMs / 1000;
                        lastVoiceSample = absoluteSample;
                        logLine(string.Format("VAD refreshed by {0} at {1:F3}s graceMs={2}", activeToneName, absoluteSample / (double)sampleRate, VadStartGraceMs));
                    }
                    return;
                }
                active = true;
                activeToneName = nextToneName;
                voiceSeen = false;
                startSample = absoluteSample;
                graceUntilSample = absoluteSample + sampleRate * VadStartGraceMs / 1000;
                lastVoiceSample = absoluteSample;
                logLine(string.Format("VAD armed by {0} at {1:F3}s graceMs={2}", activeToneName, absoluteSample / (double)sampleRate, VadStartGraceMs));
            }

            public void OnStop()
            {
                active = false;
                activeToneName = string.Empty;
                voiceSeen = false;
            }

            public IEnumerable<VadEvent> Observe(float[] samples, long blockEndSample)
            {
                if (!active || samples.Length == 0)
                {
                    yield break;
                }

                double rms = Math.Sqrt(samples.Select(x => (double)x * x).DefaultIfEmpty(0.0).Average());

                if (blockEndSample >= graceUntilSample && rms >= VadVoiceRms)
                {
                    voiceSeen = true;
                    lastVoiceSample = blockEndSample;
                    yield break;
                }

                if (voiceSeen)
                {
                    long silenceSamples = blockEndSample - lastVoiceSample;
                    if (silenceSamples >= sampleRate * VadSilenceMs / 1000)
                    {
                        active = false;
                        voiceSeen = false;
                        yield return new VadEvent("STOP", blockEndSample / (double)sampleRate, (int)Math.Round(silenceSamples * 1000.0 / sampleRate));
                    }
                    yield break;
                }

                long idleSamples = blockEndSample - startSample;
                if (idleSamples >= sampleRate * VadNoSpeechStopMs / 1000)
                {
                    active = false;
                    voiceSeen = false;
                    yield return new VadEvent("STOP", blockEndSample / (double)sampleRate, (int)Math.Round(idleSamples * 1000.0 / sampleRate));
                }
            }
        }

        private sealed class PowerResult
        {
            public PowerResult(int bit, double power)
            {
                Bit = bit;
                Power = power;
            }

            public int Bit { get; private set; }
            public double Power { get; private set; }
        }

        private sealed class BestResult
        {
            public BestResult(int bit, double power, double secondPower)
            {
                Bit = bit;
                Power = power;
                SecondPower = secondPower;
            }

            public int Bit { get; private set; }
            public double Power { get; private set; }
            public double SecondPower { get; private set; }
        }

        private sealed class ToneEvent
        {
            private readonly string name;
            private readonly double timeSeconds;
            private readonly double score;

            public ToneEvent(string name, double timeSeconds, double score)
            {
                this.name = name;
                this.timeSeconds = timeSeconds;
                this.score = score;
            }

            public string Name { get { return name; } }
            public double TimeSeconds { get { return timeSeconds; } }
            public double Score { get { return score; } }
        }

        private sealed class VadEvent
        {
            private readonly string name;
            private readonly double timeSeconds;
            private readonly int metricMs;

            public VadEvent(string name, double timeSeconds, int metricMs)
            {
                this.name = name;
                this.timeSeconds = timeSeconds;
                this.metricMs = metricMs;
            }

            public string Name { get { return name; } }
            public double TimeSeconds { get { return timeSeconds; } }
            public int MetricMs { get { return metricMs; } }
        }
    }
}

