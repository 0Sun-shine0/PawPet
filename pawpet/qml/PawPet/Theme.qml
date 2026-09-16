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

    // ------------------------------------------------------- 用户定制配色
    /* 「通过对话定制主题」的读取入口。

       颜色值不再写死在这里，而是从 Backend 拿（存在数据目录的 theme.json）。
       这样才能做到：不碰任何源码、改完立刻生效（走 Qt 属性通知）、
       打包后也能用（打包后资源目录是只读的，数据目录才写得进去）。

       为什么不用「用户放一个 Theme.qml 覆盖」那种做法：Theme.qml 带
       pragma Singleton，模块解析对同名类型是「先找到的赢」，而导入路径
       顺序不可靠 —— 靠它做覆盖是碰运气。而且打包后根本没有 .qml 文件可改。

       兜底值就是下面那些字面量：Backend 还没建好的那一瞬间 QML 可能
       已经在求值了，这时候不能返回 undefined，否则界面上会出现
       「颜色未定义」的一堆警告和黑块。 */
    function pick(key, fallback) {
        if (!backend)
            return fallback
        var map = backend.themeColors
        if (!map)
            return fallback
        var value = map[key]
        return (value && String(value).length > 0) ? value : fallback
    }

    // ---------------------------------------------------------- 界面配色
    /* 粉白暖色系。
       默认值原来在这里写死；现在作为 pick() 的兜底值保留在调用处。

       配色逻辑：
       * 底色是很淡的粉白（不是纯白，纯白看久了刺眼）
       * 文字用暖调的深灰紫，不用纯黑 —— 纯黑压在粉底上显得硬
       * 强调色用暖粉 + 蜜桃，和形象上的腮红、耳朵呼应
       * 每个功能色（专注/休息/提醒）都配一个 soft 版本当浅底，
         浅底 + 同色系深字，比「深底 + 白字」柔和得多

       用户能在设置里改 17 项（见 pawpet/theme.py 的 ROLES）。
       rose / roseSoft 刻意不开放：错误色如果被改成和背景相近，
       用户就看不到失败了。 */
    readonly property color bg:          pick("bg",          "#fdf7f9")
    readonly property color surface:     pick("surface",     "#ffffff")
    readonly property color surfaceAlt:  pick("surfaceAlt",  "#fdf1f5")
    readonly property color surfaceHi:   pick("surfaceHi",   "#fbe6ee")
    readonly property color border:      pick("border",      "#f0d4e0")
    readonly property color borderSoft:  pick("borderSoft",  "#f7e4ec")

    readonly property color text:        pick("text",        "#4a3b45")
    readonly property color textDim:     pick("textDim",     "#7d6577")
    readonly property color textFaint:   pick("textFaint",   "#9a8494")
    readonly property color accent:      pick("accent",      "#f4879f")
    readonly property color accentSoft:  pick("accentSoft",  "#fde8ee")
    readonly property color violet:      pick("violet",      "#b48ae0")
    readonly property color violetSoft:  pick("violetSoft",  "#f3eafd")
    readonly property color mint:        pick("mint",        "#5fc4ad")
    readonly property color mintSoft:    pick("mintSoft",    "#e6f7f2")
    readonly property color gold:        pick("gold",        "#e8ab4f")
    readonly property color goldSoft:    pick("goldSoft",    "#fdf3e2")
    // 错误色固定，不给用户改（见上面说明）
    readonly property color rose:        "#e8607a"
    readonly property color roseSoft:    "#fdeaee"
    readonly property color focusColor:  pick("accent",      "#f4879f")
    readonly property color shortColor:  pick("mint",        "#5fc4ad")
    readonly property color longColor:   pick("violet",      "#b48ae0")

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
    /* 字体名从 Backend 读，**唯一来源是 pawpet/qmlfont.py**。

       以前这里直接写 "Microsoft YaHei UI" / "Consolas"，Python 那边
       （markdown.py 转 HTML、测试脚本查字形）也各写一份，三处会漂移 ——
       而且漂移了不报错，只是界面上悄悄用回旧字体。

       三个都要带回退链，注意逗号分隔：
       * font       —— 雅黑 UI → 雅黑 → Segoe UI
       * fontMono   —— Consolas 里**没有汉字**。界面上「45 分钟」
                       「未配置」这类「数字 + 中文量词」的混排，不写回退
                       汉字会被 Qt 丢给 SimSun 一类的衬线体，同一行里
                       两种字形两种基线，看起来就是「字体怪怪的」。
       * fontLatin  —— Segoe UI 里也没有汉字，而且缺一堆几何符号
                       （◉ ☑ ✕ ⚙）。按钮上的图标字符会各自回退到
                       不同字体，字重和大小都不一致。
       实测补回退链不改变排版宽度（「45 分钟」59px → 59px）。 */
    readonly property string font:      backend ? backend.fontFamily      : "Microsoft YaHei UI"
    readonly property string fontMono:  backend ? backend.fontFamilyMono  : "Consolas, Microsoft YaHei UI"
    readonly property string fontLatin: backend ? backend.fontFamilyLatin : "Segoe UI, Microsoft YaHei UI"

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
