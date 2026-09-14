pragma Singleton
import QtQuick

/* 设计系统：颜色、字号、圆角、间距。
   集中在这里，改一处全局生效。 */
QtObject {
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

    readonly property int fsTiny:   10
    readonly property int fsSmall:  11
    readonly property int fsBody:   13
    readonly property int fsTitle:  16
    readonly property int fsH1:     22
    readonly property int fsClock:  54

    // ---------------------------------------------------------- 形状
    readonly property int radiusSm: 8
    readonly property int radiusMd: 12
    readonly property int radiusLg: 18
    readonly property int radiusXl: 26
    readonly property int gap:      12
    readonly property int gapLg:    18
    readonly property int pad:      18

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
