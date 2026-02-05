# Windows TRCC UI Hierarchy & Coordinates

> Comprehensive analysis of decompiled C# sources from TRCC_decompiled/
> **Note**: THREE patterns exist for BackgroundImage assignment (see below)

## Quick Reference

```
Form1 (1454x800)
├── UCDevice (0,0) 180x800 - Device sidebar
└── Content Area (180,0) 1274x800
    └── FormCZTV (0,0) 1274x800 - LCD controller per device
        ├── UCScreenImageBK (16,88) 500x500 - Preview frame
        │   └── UCScreenImage (90,130) 320x240 - LCD canvas
        ├── UCBoFangQiKongZhi (16,650) 500x56 - Video controls
        ├── Tab Buttons (y=90): BDZT(542), YDMB(612), YDZT(682), ZTSZ(882)
        └── Panel Container (532,128) 732x652
            ├── UCThemeLocal - Local themes
            ├── UCThemeWeb - Cloud themes
            └── UCThemeSetting 732x661 - Settings
                ├── UCXiTongXianShi (10,1) 472x430 - Overlay grid
                ├── UCXiTongXianShiColor (492,1) 230x374 - Color picker
                ├── UCMengBanXianShi (10,441) 351x100 - Layout mask
                ├── UCBeiJingXianShi (371,441) 351x100 - Background
                ├── UCTouPingXianShi (10,551) 351x100 - Screen cast
                └── UCShiPingBoFangQi (371,551) 351x100 - Video player
```

## Background Image Assignment Patterns

**THREE PATTERNS exist:**

### Pattern 1: Self-set defaults (in component's own .cs file)
Components set their OWN default BackgroundImage in `InitializeComponent()`. These may be:
- **Static** (single resource, never changes)
- **Type-dependent** (changes based on component type/state)
- **Language-dependent** (changes based on language setting)

**TRCC namespace (main app):**
| Component | Resource | Line | Notes |
|-----------|----------|------|-------|
| Form1 | A0无设备 | 1709 | No-device background |
| FormStart | A0启动界面 | 103 | Splash screen |
| FormSystemInfo | P0系统信息 | 425 | System info dialog |
| UCAbout | A0关于{lang} | 397-429 | Language-dependent (self-managed) |
| UCDevice | A0硬件列表 | 1425 | Device sidebar |
| UCSystemInfoOptions | A0数据列表 | 597 | Data list panel |

**TRCC.CZTV namespace (LCD controller):**
| Component | Resource | Line | Notes |
|-----------|----------|------|-------|
| FormCZTV | P0CZTV{lang} | 461-589, 7177 | Language-dependent (self-managed) |
| FormScreenImage | P0预览弹窗800X360 | 125 | Preview popup |

**TRCC.DCUserControl namespace (reusable components):**
| Component | Resource | Line | Notes |
|-----------|----------|------|-------|
| UCBeiJingXianShi | P01背景显示 | 198 | Background sub-panel |
| UCBoFangQiKongZhi | P0播放器控制 | 1864 | Video control bar |
| UCButton | A1CZTVa | 38 | Device button |
| UCColorA | D3旋钮 | 283 | Color knob |
| UCDingYiWenBen | P01自定文字 | 201 | Custom text panel |
| UCDongHuaLianDong | P01动画联动 | 374 | Animation settings |
| UCImageCut | P0图片裁减320240 | 2009 | Image crop tool |
| UCInfoImage | P0M1 | 186 | Info image |
| UCJianPanLianDongA | P01键盘联动1 | 108 | Keyboard link 1 |
| UCJianPanLianDongB | P01键盘联动2 | 165 | Keyboard link 2 |
| UCJianPanLianDongC | P01键盘联动3 | 183 | Keyboard link 3 |
| UCMengBanXianShi | P01布局蒙板 | 137 | Layout mask sub-panel |
| UCScreenImageBK | P预览320X240 | 43 | Preview frame (resolution-dependent) |
| UCScreenLED | DLF13 | 10133 | LED screen |
| UCShiJianXianShi | P01时间显示 | 792 | Time display panel |
| UCShiPingBoFangQi | P01播放器 | 116 | Video player sub-panel |
| UCSystemInfoOptionsOne | A{type} | 80-108 | Type-dependent icons |
| UCThemeLocal | P0本地主题 | 759 | Local themes panel |
| UCThemeMask | p0云端主题 | 329 | Cloud mask panel |
| UCThemeSetting | P0主题设置 | 395 | Settings container |
| UCThemeWeb | p0云端背景 | 590 | Cloud themes panel |
| UCTouPingXianShi | P01投屏显示xy{lang} | 96-165, 655 | Screen cast (lang + type-dependent) |
| UCVideoCut | P0裁减320320 | 2485 | Video crop tool |
| UCXiTongXianShi | P01内容 | 395 | Overlay grid |
| UCXiTongXianShiAdd | P01增加内容 | 555 | Add overlay panel |
| UCXiTongXianShiColor | P01参数面板 | 1103 | Color/font picker |
| UCXiTongXianShiSub | P数据/时间/星期/日期/文本 | 60-110, 343 | Type-dependent |
| UCXiTongXianShiTable | P01模块设置 | 262 | Module settings |
| UCXiTongXinXi | P01系统信息 | 944 | System info sub-panel |

**TRCC.LED namespace:**
| Component | Resource | Line | Notes |
|-----------|----------|------|-------|
| FormLED | D0数码屏{lang}/D0LF{type}{lang} | 1133-1591 | Device + language-dependent |

**TRCC.KVMALED6 namespace:**
| Component | Resource | Line | Notes |
|-----------|----------|------|-------|
| FormKVMALED6 | D0KVMA灯控 | 2023 | KVM LED controller |

### Pattern 2: Embedded in parent's .resx (static)
Some backgrounds are binary-embedded in the **parent's** .resx file:

**FormCZTV.resx embeds:**
| Component | Notes |
|-----------|-------|
| ucVideoCut1.BackgroundImage | Video crop tool (may differ from self-set default) |
| ucBoFangQiKongZhi1.BackgroundImage | Video control bar |

**UCThemeSetting.resx embeds:**
| Component | Notes |
|-----------|-------|
| ucXiTongXianShi1.BackgroundImage | Overlay grid |
| ucXiTongXianShiColor1.BackgroundImage | Color picker |
| ucXiTongXianShiAdd1.BackgroundImage | Add overlay panel |
| ucXiTongXianShiTable1.BackgroundImage | Text/value table |

### Pattern 3: Parent OVERWRITES child backgrounds (language-dependent)
`FormCZTV.FormCZTVLanguageSet()` dynamically overwrites child component backgrounds based on language:

| Child Component | Resource Pattern | Notes |
|-----------------|------------------|-------|
| self | P0CZTV{lang} | Also self-managed in Pattern 1 |
| ucThemeLocal1 | P0本地主题{lang} | Overwrites self-set default |
| ucThemeWeb1 | p0云端背景{lang} | Overwrites self-set default |
| ucThemeMask1 | P0云端主题{lang} | Overwrites self-set default |
| ucThemeSetting1.ucBeiJingXianShi1 | P01背景显示{lang} | Overwrites self-set default |
| ucThemeSetting1.ucMengBanXianShi1 | P01布局蒙板{lang} | Overwrites self-set default |
| ucThemeSetting1.ucTouPingXianShi1 | P01投屏显示xy{lang} | Overwrites self-set default |
| ucThemeSetting1.ucShiPingBoFangQi1 | P01播放器{lang} | Overwrites self-set default |
| ucThemeSetting1.ucXiTongXianShi1 | P01内容{lang} | Overwrites self-set default |
| ucThemeSetting1.ucXiTongXianShiColor1 | P01参数面板{lang} | Overwrites self-set default |

### Summary: Loading Order
1. Component sets its OWN default in `InitializeComponent()` (Pattern 1)
2. Parent's .resx may override during parent's `InitializeComponent()` (Pattern 2)
3. Parent's language-set method may override dynamically (Pattern 3)

### Language Suffixes
- (none) = Simplified Chinese (default)
- en = English
- tc = Traditional Chinese
- d = German, e = Spanish, f = French, p = Portuguese, r = Russian, x = Japanese

## Detailed Component Coordinates

### Form1 (Main Shell) - 1454x800

| Component | Location | Size | Purpose |
|-----------|----------|------|---------|
| ucDevice1 | (0, 0) | 180x800 | Device sidebar |
| ucAbout1 | (180, 0) | 1274x800 | About/content area |
| buttonPower | (1392, 24) | 40x40 | Close button |
| buttonHelp | (1342, 24) | 40x40 | Help button |

### FormCZTV (LCD Controller) - 1274x800

| Component | Location | Size | Purpose |
|-----------|----------|------|---------|
| ucScreenImageBK1 | (16, 88) | 500x500 | Preview frame |
| ucBoFangQiKongZhi1 | (16, 650) | 500x56 | Video control bar |
| ucImageCut1 | (16, 88) | 500x702 | Image editor (hidden) |
| ucVideoCut1 | (16, 88) | 500x702 | Video editor (hidden) |
| ucThemeLocal1 | (532, 128) | 732x652 | Local themes panel |
| ucThemeWeb1 | (532, 128) | 732x652 | Cloud themes panel |
| ucThemeSetting1 | (532, 128) | 732x661 | Settings panel |
| buttonBDZT | (542, 90) | 50x38 | Local tab |
| buttonYDMB | (612, 90) | 50x38 | Cloud tab |
| buttonYDZT | (682, 90) | 50x38 | Cloud BG tab (hidden) |
| buttonZTSZ | (882, 90) | 50x38 | Settings tab |
| ucComboBoxA1 | (39, 680) | 108x24 | Rotation dropdown |
| buttonLDD | (157, 680) | - | Brightness button |
| textBoxCMM | (278, 684) | 102x16 | Custom mode |
| buttonBCZT | (383, 680) | 24x24 | Save theme |
| buttonDaoChu | (412, 680) | 40x24 | Export |
| buttonDaoRu | (453, 680) | 40x24 | Import |
| labelZXCG | (765, 28) | 374x27 | Status label |
| buttonHelp | (1162, 24) | 40x40 | Help |
| buttonPower | (1212, 24) | 40x40 | Close |

### UCThemeSetting (Settings Container) - 732x661

| Component | Location | Size | Purpose |
|-----------|----------|------|---------|
| ucXiTongXianShi1 | (10, 1) | 472x430 | Overlay element grid (7x6) |
| ucXiTongXianShiColor1 | (492, 1) | 230x374 | Color/font picker |
| ucXiTongXianShiAdd1 | (492, 1) | 230x430 | Add overlay (alt view) |
| ucMengBanXianShi1 | (10, 441) | 351x100 | Layout mask sub-panel |
| ucBeiJingXianShi1 | (371, 441) | 351x100 | Background sub-panel |
| ucTouPingXianShi1 | (10, 551) | 351x100 | Screen cast sub-panel |
| ucShiPingBoFangQi1 | (371, 551) | 351x100 | Video player sub-panel |
| ucXiTongXianShiTable1 | (492, 376) | 230x54 | Text/value table |
| ucDongHuaLianDong1 | (10, 690) | 682x84 | Animation settings |

### UCThemeLocal (Local Themes) - 732x652

| Component | Location | Size | Purpose |
|-----------|----------|------|---------|
| buttonAll | (21, 29) | 63x18 | All filter |
| buttonDefault | (121, 29) | 63x18 | Default filter |
| buttonUser | (221, 29) | 63x18 | User filter |
| buttonLunbo | (531, 28) | 40x17 | Slideshow toggle |
| textBoxTimer | (602, 29) | 24x16 | Timer input |
| buttonThemeOut | (651, 27) | 60x18 | Export |

### UCScreenImageBK (Preview Frame) - 500x500

| Component | Location | Size | Purpose |
|-----------|----------|------|---------|
| ucScreenImage1 | (90, 130) | 320x240 | LCD preview (default) |

**Resolution-specific offsets for ucScreenImage1:**
| Resolution | Offset | Frame Image |
|------------|--------|-------------|
| 240x240 | (130, 130) | P预览240X240.png |
| 320x240 | (90, 130) | P预览320X240.png |
| 240x320 | (130, 90) | P预览240X320.png |
| 320x320 | (90, 90) | P预览320X320.png |
| 360x360 | (70, 70) | P预览360360圆.png |
| 480x480 | (10, 10) | P预览480X480.png |

### UCBoFangQiKongZhi (Video Control Bar) - 500x56

| Component | Location | Size | Purpose |
|-----------|----------|------|---------|
| button1 | (10, 26) | 34x26 | Play/pause |
| buttonTPJCH | (64, 26) | 34x26 | Height fit |
| buttonTPJCW | (108, 26) | 34x26 | Width fit |
| labelAllTimer | (162, 26) | 88x20 | Total time |
| labelNowTimer | (274, 26) | 220x20 | Progress/current |

### UCDevice (Device Sidebar) - 180x800

| Component | Location | Size | Purpose |
|-----------|----------|------|---------|
| Device buttons | (25, 100+i*60) | 140x50 | Device selector |
| buttonSetting | (25, 730) | 140x50 | Settings |

## Resource Naming Convention

| Prefix | Usage | Example |
|--------|-------|---------|
| P0 | Main panels | P0CZTVen, P0本地主题en |
| P01 | Settings sub-panels | P01背景显示en, P01参数面板en |
| P预览 | Preview frames | P预览320X320, P预览圆形遮罩360360圆 |
| A0 | Window backgrounds | A0关于en, A0无设备en |
| A1 | Device buttons | A1CZTV, A1FROZEN_WARFRAME |
| PL | Brightness levels | PL0, PL1, PL2, PL3 |

## Linux Implementation Notes (PyQt6)

1. **Background images use QPalette, NOT stylesheets**:
   - `QPalette` + `QBrush(pixmap)` + `setAutoFillBackground(True)` for image backgrounds
   - If ANY ancestor has `setStyleSheet()`, it overrides QPalette on ALL descendants
   - See `set_background_pixmap()` in `qt_components/base.py`
2. **Three-layer background system** (same as Windows):
   - Components set default backgrounds in `__init__()` (Pattern 1)
   - Parent may override via `set_background_pixmap()` (Pattern 2)
   - `qt_app_mvc.py` `set_language()` re-applies all localized backgrounds (Pattern 3)
3. **MVC architecture**: Controllers in `core/controllers.py` are GUI-independent; views subscribe via callbacks
4. **Resolution-aware preview** - UCPreview changes frame image based on device resolution
5. **Language support** - `Assets.get_localized(base_name, lang)` maps to {lang} suffixed resources
6. **Coordinates match Windows exactly** - Layout constants in `qt_components/constants.py`
