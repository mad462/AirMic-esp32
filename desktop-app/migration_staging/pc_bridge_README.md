# AirMic 语音按键控制台

这个 Windows 工具监听 `ESP32-AirMic-HFP` 的 HFP 麦克风音频流，把固件塞进音频里的 START/STOP 短音编码转换成本机快捷键。

当前方向：

- Windows 只需要配对 `ESP32-AirMic-HFP`。
- HFP 负责麦克风音频。
- START/STOP 控制信号走 HFP 音频编码。
- BLE/GATT PTT 已不作为主链路。

## 安装

```powershell
cd "D:\FUCKIDF\AirMic esp32 hfp gattptt\pc_bridge"
python -m pip install -r requirements.txt
```

## 启动

```powershell
python .\airmic_ptt_gui.py
```

## 界面功能

- `蓝牙设置`：打开 Windows 蓝牙设置。
- `录音设备`：打开经典录音设备面板。
- `移除 AirMic`：尝试移除 AirMic 相关蓝牙/音频设备。
- `释放按键`：释放 GUI 自己按下的快捷键。
- GUI 启动后会自动启动后台监听，不需要手动点按钮。
- `重启监听`：手动重启 HFP 音频编码监听和 COM10 配置通道。
- `音频算法`：默认 `旧链路（稳定）`，只有在对比实验时才切到 `ESP-SR 降噪实验`。
- `发送 START` / `发送 STOP` / `START + STOP`：通过串口让 ESP32 注入测试编码。
- `测试快捷键`：不走 HFP 音频，直接测试当前快捷键是否能触发目标输入法。
- `3秒后测试`：点完后立刻切到目标输入框，3 秒后再自动发送快捷键，用来排除 GUI 抢焦点的问题。
- `发送模式`：可选 `扫描码` 或 `虚拟键`。如果目标输入法不吃一种模式，就切换另一种再测试。
- `最长保持(ms)`：START 后如果迟迟收不到 STOP，GUI 会自动释放自己按下的快捷键，避免 Alt / Ctrl / Win 卡住。
- `应用到设备`：把当前音频参数和当前音频算法模式临时发送给 ESP32，本轮不写入 NVS。
- `读取当前值`：让 ESP32 打印当前参数。
- `恢复默认`：恢复 ESP32 默认音频参数，本轮不写入 NVS。

## 快捷键

可选项：

- `右 Alt`
- `左 Alt`
- `Ctrl + Win`
- `禁用`

如果日志显示已经收到 START/STOP，但目标输入法没有反应，先点 `测试快捷键`。如果测试也不触发，问题通常在目标输入法快捷键、窗口焦点或权限，不在 HFP 音频解码。

建议排查顺序：

1. 选择目标快捷键。
2. 选择 `扫描码`，点 `3秒后测试`，立刻切到目标输入框。
3. 如果无效，切换为 `虚拟键` 再试。
4. 如果仍无效，再换 `右 Alt` / `左 Alt` / `Ctrl + Win`。

## 音频参数

- `麦克风增益`：Q8 定点增益，`256 = 1.00x`，`512 = 2.00x`，`1024 = 4.00x`，最大 `4096 = 16.00x`。
- `噪声门`：低于阈值的 PCM 样本静音。建议先保持 `0`。
- `编码音量`：START/STOP 音频编码的音量。识别稳定时可以降低，识别弱时可以提高。
- `采样右移`：INMP441 32 位样本转 16 位 PCM 时的右移位数。默认 `14`；数值越小声音越大。建议从 `14 -> 13 -> 12 -> 11` 逐步试，看到 ESP32 日志 peak 接近 `32767` 或听到明显破音就调回去。

注意：配置命令需要 COM10。为了减少写 NVS 和串口状态变化导致的断联/重启，本轮 GUI 不发送 `cfg save`，所有音频参数都是运行时临时参数；如果日志提示 COM10 未连接，请先点 `重启监听`，等串口打开后再应用。

## 验证

```powershell
cd "D:\FUCKIDF\AirMic esp32 hfp gattptt"
python -m py_compile .\pc_bridge\airmic_ptt_bridge.py .\pc_bridge\airmic_ptt_gui.py
cd .\pc_bridge
python -m unittest -v test_tone_shortcut.py
```

## 已知限制

- 当前没有虚拟麦克风转发，所以 START/STOP 编码仍会进入原始 HFP 麦克风音频。
- COM10 只用于本地调试和调参；最终无线控制仍来自 HFP 音频编码。
- 不使用 VAD 自动释放，STOP 应由固件音频编码发出；GUI 另外有最长保持时间兜底，并保留 `释放按键` 作为手动保险。
