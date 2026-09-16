pragma Singleton
import QtQuick

/* 设计系统：颜色、字号、圆角、间距。
   集中在这里，改一处全局生效。 */
QtObject {
    // ---------------------------------------------------------- 界面缩放
    /* 整体缩放系数。
       字号和间距原来写死成 10/11/13，是按小窗口调的 ——
       在 1920x1080 的笔记本屏（物理约 157 DPI）上看起来会明显偏小，
       右边还会空出一大片。
       现在所有尺寸都乘这个系数，用户在设置里能调，默认按屏幕分辨率自动选。
       Backend.uiScale 是唯一来源（Python 侧算好，QML 只读）。 */
    readonly property real scale: {
        var value = backend ? backend.uiScale : 1.0
        return (value && value > 0.1) ? value : 1.0
    }

    // 按缩放系数换算尺寸的小工具
    function px(value) {
        return Math.round(value * scale)
    }

    // ---------------------------------------------------------- 界面配色
    readonly property color bg:          "#14121c"
    readonly property color surface:     "#1e1b2b"
    readonly property color surfaceAlt:  "#262238"
    readonly property color surfaceHi:   "#2f2a44"
    readonly property color border:      "#3a3352"
    readonly property color borderSoft:  "#2b2540"

    readonly property color text:        "#f2ecff"
    readonly property color textDim:     "#a99fc4"
    readonly property color textFaint:   "#6f6790"

    readonly property color accent:      "#ff9d6c"
    readonly property color accentSoft:  "#3a2a2e"
    readonly property color violet:      "#8b7bf0"
    readonly property color violetSoft:  "#2b2647"
    readonly property color mint:        "#5fd0b8"
    readonly property color gold:        "#ffd76a"
    readonly property color rose:        "#ff6b81"

    readonly property color focusColor:  "#ff9d6c"
    readonly property color shortColor:  "#5fd0b8"
    readonly property color longColor:   "#8b7bf0"

    // ---------------------------------------------------------- 宠物配色
    readonly property color petFur:       "#fff3e5"
    readonly property color petFurShade:  "#f2dcc6"
    readonly property color petEar:       "#ef9a75"
    readonly property color petEarInner:  "#ffd0bc"
    readonly property color petOutline:   "#4b4054"
    readonly property color petEye:       "#3b3350"
    readonly property color petBlush:     "#f3aaa0"
    readonly property color petScarf:     "#3f9e9b"
    readonly property color petScarfHi:   "#4bb8b0"
    readonly property color petNose:      "#d56f72"
    readonly property color petShadow:    "#2a2338"
    readonly property color petBadgeBg:   "#ffd76a"
    readonly property color petBadgeEdge: "#c08b52"

    // ---------------------------------------------------------- 排版
    readonly property string font:      "Microsoft YaHei UI"
    readonly property string fontMono:  "Consolas"
    readonly property string fontLatin: "Segoe UI"

    // 字号。基准值按 100% 缩放定，实际用的时候乘 scale。
    // 注意 fsTiny 原来是 10 —— 在高分屏上小到几乎看不清，提到 11。
    readonly property int fsTiny:   px(11)
    readonly property int fsSmall:  px(12)
    readonly property int fsBody:   px(14)
    readonly property int fsTitle:  px(17)
    readonly property int fsH1:     px(23)
    readonly property int fsClock:  px(54)

    // ---------------------------------------------------------- 形状
    readonly property int radiusSm: px(8)
    readonly property int radiusMd: px(12)
    readonly property int radiusLg: px(18)
    readonly property int radiusXl: px(26)
    readonly property int gap:      px(12)
    readonly property int gapLg:    px(18)
    readonly property int pad:      px(18)

    readonly property int animFast:   120
    readonly property int animNormal: 200
    readonly property int animSlow:   380

    // 缓动曲线：进出都用同一套，看起来更「顺」
    readonly property int easing: Easing.OutCubic

    function modeColor(mode) {
        if (mode === "short_break") return shortColor
        if (mode === "long_break") return longColor
        return focusColor
    }
}
