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
    /* 粉白暖色系。
       原来是深紫黑（#14121c），偏「开发者工具」的味道；泛用户第一次打开
       会觉得冷、有距离感。改成粉白之后更像日常小软件，配小爪这个形象也更搭。

       配色逻辑：
       * 底色是很淡的粉白（不是纯白，纯白看久了刺眼）
       * 文字用暖调的深灰紫，不用纯黑 —— 纯黑压在粉底上显得硬
       * 强调色用暖粉 + 蜜桃，和形象上的腮红、耳朵呼应
       * 每个功能色（专注/休息/提醒）都配一个 soft 版本当浅底，
         浅底 + 同色系深字，比「深底 + 白字」柔和得多 */
    readonly property color bg:          "#fdf7f9"   // 页面底：很淡的粉白
    readonly property color surface:     "#ffffff"   // 卡片：纯白浮在粉底上
    readonly property color surfaceAlt:  "#fdf1f5"   // 次级块：再淡一点的粉
    readonly property color surfaceHi:   "#fbe6ee"   // 高亮/悬停
    readonly property color border:      "#f0d4e0"   // 描边：淡粉
    readonly property color borderSoft:  "#f7e4ec"   // 更淡的描边

    readonly property color text:        "#4a3b45"   // 正文：暖深灰紫，不是纯黑
    readonly property color textDim:     "#7d6577"   // 次要文字
    // 最弱一级：说明、时间戳、收尾交代。
    // 原来定的是 #b09aa6 —— 深色主题上「弱」是暗下去，浅色主题上「弱」
    // 是**发灰变淡**，压在白底上只有 2.8:1 的对比度，实机截图里看着发虚。
    // 提到 #9a8494 之后还有层次感，但读得清了。
    readonly property color textFaint:   "#9a8494"

    readonly property color accent:      "#f4879f"   // 主强调：暖粉
    readonly property color accentSoft:  "#fde8ee"   // 主强调的浅底
    readonly property color violet:      "#b48ae0"   // 紫（休息/次要）
    readonly property color violetSoft:  "#f3eafd"
    readonly property color mint:        "#5fc4ad"   // 薄荷（完成/成功）
    readonly property color mintSoft:    "#e6f7f2"
    readonly property color gold:        "#e8ab4f"   // 琥珀（提醒/警告）
    readonly property color goldSoft:    "#fdf3e2"
    readonly property color rose:        "#e8607a"   // 玫红（错误/危险）
    readonly property color roseSoft:    "#fdeaee"

    readonly property color focusColor:  "#f4879f"
    readonly property color shortColor:  "#5fc4ad"
    readonly property color longColor:   "#b48ae0"

    // ---------------------------------------------------------- 宠物配色
    // 形象本身是奶白+蜜桃的小猫，配粉白界面正合适，微调一下描边让它
    // 在浅底上仍然立得住（原来描边是深紫，压在粉底上会显脏）。
    readonly property color petFur:       "#fff6ef"
    readonly property color petFurShade:  "#f6e2d3"
    readonly property color petEar:       "#f2a181"
    readonly property color petEarInner:  "#ffd6c4"
    readonly property color petOutline:   "#7a6070"
    readonly property color petEye:       "#5c4a58"
    readonly property color petBlush:     "#f7b3aa"
    readonly property color petScarf:     "#59b3ae"
    readonly property color petScarfHi:   "#6cc7c1"
    readonly property color petNose:      "#de8184"
    readonly property color petShadow:    "#e8d3dd"
    readonly property color petBadgeBg:   "#ffd98a"
    readonly property color petBadgeEdge: "#d3a163"

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
    // 圆角比原来更大一点：粉白配色 + 大圆角 = 更「软」的观感
    readonly property int radiusSm: px(10)
    readonly property int radiusMd: px(14)
    readonly property int radiusLg: px(20)
    readonly property int radiusXl: px(28)
    readonly property int gap:      px(12)
    readonly property int gapLg:    px(18)
    readonly property int pad:      px(18)

    // 轻柔投影。浅色界面靠阴影分层，比描边自然。
    readonly property color shadowColor: "#26b08a9a"   // 带透明度的暖粉灰
    readonly property int   shadowY:     px(2)
    readonly property int   shadowBlur:  px(10)

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
