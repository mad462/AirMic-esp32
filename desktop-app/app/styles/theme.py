from __future__ import annotations

from pathlib import Path

from app.styles.scaling import DesignScaleContext


def build_app_stylesheet(scale: DesignScaleContext) -> str:
    """
    生成整个 PySide6 应用的全局样式表。

    这份样式表的设计思路是：
    1. 先把“可调参数”集中在函数开头，方便你自己改；
    2. 再把这些参数插入到 QSS 字符串里；
    3. 所有尺寸都尽量通过 scale 做一层缩放，兼容 Windows 100% / 200% 显示。

    你后面如果只想改观感，优先改下面这批变量，不要一上来就改 QSS 本体。
    """

    # 图标资源目录。这里主要给下拉箭头读取 svg 用。
    icons_dir = Path(__file__).resolve().parents[1] / "assets" / "icons"
    caret_down = icons_dir / "caret-down.svg"

    # ------------------------------------------------------------------
    # 一、字体分组
    # ------------------------------------------------------------------
    # 标题字号：用于主标题 / 调试台标题
    title_size = scale.scale_font(24)

    # 正文字号：用于主界面状态行、Tone 行、调试项标题等
    body_font = scale.scale_font(18)
    body_small_font = scale.scale_font(15)

    # 标注字号：用于次级说明、小标签、日志等
    meta_font = scale.scale_font(13)

    # 基础默认字号：如果某个控件没有单独指定字体，会走这里
    base_font = scale.scale_font(14)

    # ------------------------------------------------------------------
    # 二、颜色分组
    # ------------------------------------------------------------------
    # 激活颜色：蓝色，表示可交互 / 激活 / 选中 / 当前值
    color_active = "#4D9AFF"

    # 正常颜色：黑色，表示主要文本
    color_normal = "#000000"

    # 不生效颜色：灰色，表示未激活 / 次级信息 / 图标
    color_muted = "#838D9A"

    # 下面是一些辅助色，通常不需要频繁改
    color_window_bg = "transparent"  # 整个窗口外层保持透明，露出圆角和阴影
    color_card_bg = "#FFFFFF"        # 白色卡片背景
    color_divider = "#E6EBF2"        # 分割线颜色
    color_combo_border = "#DBE3EF"   # 下拉面板边框
    color_slider_bg = "#DFE5F0"      # 滑杆轨道底色
    color_button_secondary = "#C5CBD6"
    color_button_secondary_hover = "#B8BFCB"
    color_primary_hover = "#3F84EB"
    color_log_text = "#C3CAD6"
    color_log_border = "#8C96A8"

    # ------------------------------------------------------------------
    # 三、圆角 / 间距 / 图标尺寸
    # ------------------------------------------------------------------
    # 大卡片圆角：主控制台、调试台白色卡片
    card_radius = scale.scale_value(20)

    # 普通按钮圆角：调试台底部按钮
    button_radius = scale.scale_value(10)

    # 日志框圆角
    log_radius = scale.scale_value(10)

    # 图标按钮尺寸：右上角齿轮 / 关闭
    icon_button = scale.scale_value(26)

    # 播放测试图标尺寸
    tone_test_icon = scale.scale_value(28)

    # ------------------------------------------------------------------
    # 四、调试台控件尺寸
    # -------------------------------------------------------handle_size = scale.scale_value-----------
    field_min_width = scale.scale_value(88)       # 调试台左侧标签最小宽度
    combo_min_width = scale.scale_value(88)       # 端口下拉框最小宽度
    combo_padding_right = scale.scale_value(20)   # 下拉框右侧箭头预留
    combo_drop_width = scale.scale_value(24)      # 下拉框点击箭头区域宽度
    combo_arrow = scale.scale_value(12)           # 下拉箭头 svg 尺寸

    debug_value_min_width = scale.scale_value(1) # 调试台右侧输入值宽度

    # 滑杆相关参数
    groove_height = scale.scale_value(10)
    groove_radius = scale.scale_value(5)
    groove_margin = scale.scale_value(10)
    handle_size = scale.scale_value(5)
    handle_radius = scale.scale_value(7)
    handle_margin = scale.scale_value(10)
    handle_border = scale.scale_value(0)

    # 按钮 padding / 字号
    button_pad_y = scale.scale_value(9)
    button_pad_x = scale.scale_value(18)
    button_font = scale.scale_font(14)

    # 日志框边框 / padding / 字号
    log_border = scale.scale_value(2)
    log_pad_y = scale.scale_value(12)
    log_pad_x = scale.scale_value(14)
    log_font = scale.scale_font(13)

    return f"""
QWidget {{
    color: {color_normal};
    font-family: "Microsoft YaHei UI";
    font-size: {base_font}px;
    background: transparent;
}}

QMainWindow {{
    background: {color_window_bg};
}}

#mainRoot, #debugRoot {{
    background: {color_window_bg};
}}

/* 主控制台 / 调试台的白色圆角卡片 */
#consoleCard, #debugCard {{
    background: {color_card_bg};
    border: 1px solid #EEF2F7;
    border-radius: {card_radius}px;
}}

/* 主标题 */
#consoleTitle, #debugTitle {{
    font-size: {title_size}px;
    font-weight: 700;
    color: {color_normal};
    background: transparent;
}}

/* 右上角齿轮 / 关闭图标按钮 */
#debugButton, #closeButton {{
    background: transparent;
    border: none;
    color: {color_muted};
    padding: 0;
    min-width: {icon_button}px;
    min-height: {icon_button}px;
    max-width: {icon_button}px;
    max-height: {icon_button}px;
}}

#debugButton:hover, #closeButton:hover {{
    color: {color_muted};
}}

/* 行分割线 */
#consoleDivider, #debugDivider {{
    background: {color_divider};
    color: {color_divider};
    border: none;
    min-height: 1px;
    max-height: 1px;
}}

/* 状态行外层按钮：只保留点击能力，不显示按钮外观 */
QPushButton[statusLink="true"] {{
    background: transparent;
    border: none;
    padding: 0;
    text-align: left;
}}

/* 状态标题，例如“监听服务 / 设备状态 / 系统输入” */
#statusTitle {{
    color: {color_normal};
    font-size: {body_small_font}px;
    font-weight: 500;
    background: transparent;
}}

/* 状态值激活态，例如“正常 / 在线 / AirMic HFP麦克风” */
#statusValue {{
    color: {color_active};
    font-size: {body_small_font}px;
    font-weight: 500;
    background: transparent;
}}

/* 状态值未激活态：如果以后你想让某些状态变灰，就让 objectName 走这个 */
#statusValueMuted {{
    color: {color_muted};
    font-size: {body_small_font}px;
    font-weight: 500;
    background: transparent;
}}

/* Tone 行左侧标题，例如 Start Tone / A Tone */
#toneName {{
    font-size: {body_font}px;
    font-weight: 500;
    color: {color_normal};
    background: transparent;
}}

/* Tone 行右侧显示文本，例如 right Alt / 点击录制 */
#toneValueStatic,
#toneRecordButton {{
    background: transparent;
    border: none;
    color: {color_muted};
    font-size: {body_font}px;
    font-weight: 400;
    text-align: left;
    padding: 0;
}}

#toneValueStatic:disabled {{
    color: {color_muted};
}}

#toneRecordButton:hover {{
    color: {color_active};
}}

#toneRecordButton[recording="true"] {{
    color: {color_active};
}}

/* 每一行右侧的测试播放图标 */
#toneTestButton {{
    background: transparent;
    border: none;
    color: {color_muted};
    padding: 0;
    min-width: {tone_test_icon}px;
    min-height: {tone_test_icon}px;
    max-width: {tone_test_icon}px;
    max-height: {tone_test_icon}px;
}}

#toneTestButton:hover {{
    color: {color_muted};
}}

/* 调试台：端口 / 设备串口状态 左侧标题 */
#debugMetaLabel {{
    color: {color_normal};
    font-size: {meta_font}px;
    font-weight: 500;
}}

/* 调试台：端口值 / 串口状态值 */
#debugMetaValue {{
    color: {color_active};
    font-size: {meta_font}px;
    font-weight: 500;
}}

/* 调试台小节标题，例如“麦克风设置” */
#debugSectionTitle {{
    color: {color_muted};
    font-size: {meta_font}px;
    font-weight: 500;
    margin-bottom: {scale.scale_value(6)}px;
}}

/* 调试台左侧字段名，例如“麦克增益 / 采样右移” */
#debugFieldLabel {{
    color: {color_normal};
    font-size: {body_small_font}px;
    font-weight: 500;
    min-width: {field_min_width}px;
}}

/* 调试台右侧可输入数值 */
#debugValueInput {{
    background: transparent;
    border: none;
    color: {color_active};
    font-size: {body_small_font}px;
    font-weight: 500;
    padding: 0;
    min-width: {debug_value_min_width}px;
}}

/* 调试台端口下拉框 */
QComboBox#debugPortCombo {{
    background: transparent;
    border: none;
    color: {color_active};
    font-size: {body_small_font}px;
    font-weight: 500;
    min-width: {combo_min_width}px;
    padding: 0 {combo_padding_right}px 0 0;
}}

QComboBox#debugPortCombo::drop-down {{
    border: none;
    width: {combo_drop_width}px;
}}

QComboBox#debugPortCombo::down-arrow {{
    image: url("{caret_down.as_posix()}");
    width: {combo_arrow}px;
    height: {combo_arrow}px;
}}

/* 下拉展开面板 */
QComboBox QAbstractItemView {{
    background: {color_card_bg};
    border: 1px solid {color_combo_border};
    border-radius: {scale.scale_value(12)}px;
    padding: {scale.scale_value(6)}px;
    selection-background-color: #eef5ff;
    selection-color: {color_normal};
}}

QMenu#trayMenu {{
    background: {color_card_bg};
    color: {color_normal};
    border: 1px solid {color_combo_border};
    border-radius: {scale.scale_value(12)}px;
    padding: {scale.scale_value(6)}px 0;
}}

QMenu#trayMenu::item {{
    background: transparent;
    color: {color_normal};
    padding: {scale.scale_value(8)}px {scale.scale_value(14)}px;
    margin: 0 {scale.scale_value(4)}px;
    border-radius: {scale.scale_value(8)}px;
}}

QMenu#trayMenu::item:selected {{
    background: #eef5ff;
    color: {color_normal};
}}

QMenu#trayMenu::item:disabled {{
    color: {color_muted};
    background: transparent;
}}

/* 调试台滑杆轨道 */
QSlider#debugSlider::groove:horizontal {{
    border: none;
    height: {groove_height}px;
    background: {color_slider_bg};
    border-radius: {groove_radius}px;
    margin: {groove_margin}px 0;
}}

/* 调试台滑杆圆点 */
QSlider#debugSlider::handle:horizontal {{
    width: {handle_size}px;
    height: {handle_size}px;
    margin: -{handle_margin}px 0;
    border-radius: {handle_radius}px;
    background: {color_active};
    border: {handle_border}px solid transparent;
}}

/* 调试台底部按钮 */
QPushButton#primaryButton,
QPushButton#secondaryButton {{
    border: none;
    border-radius: {button_radius}px;
    padding: {button_pad_y}px {button_pad_x}px;
    font-size: {button_font}px;
    font-weight: 600;
}}

QPushButton#primaryButton {{
    background: {color_active};
    color: #ffffff;
}}

QPushButton#primaryButton:hover {{
    background: {color_primary_hover};
}}

QPushButton#secondaryButton {{
    background: {color_button_secondary};
    color: #ffffff;
}}

QPushButton#secondaryButton:hover {{
    background: {color_button_secondary_hover};
}}

/* 日志框 */
QPlainTextEdit#debugLogView {{
    background: {color_card_bg};
    color: {color_log_text};
    border: {log_border}px solid {color_log_border};
    border-radius: {log_radius}px;
    padding: {log_pad_y}px {log_pad_x}px;
    font-size: {log_font}px;
    line-height: 1.6;
    selection-background-color: #dbeafe;
    selection-color: {color_normal};
}}
"""
