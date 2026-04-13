#!/usr/bin/env python3
"""Rename GUI assets from C# Chinese names to English panel-based names.

Reusable tool: run after extracting new .resx images from a Thermalright C#
update.  Maps Chinese asset base names → English names following the convention
``{panel}_{purpose}.png``, renames files + updates all code references.

Usage::

    # Preview what would change (no files touched):
    python tools/rename_assets.py --dry-run

    # Do it for real:
    python tools/rename_assets.py

    # Show unmapped assets (new files from a C# update):
    python tools/rename_assets.py --unmapped
"""
from __future__ import annotations

import argparse
from pathlib import Path

# ============================================================================
# Paths
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / 'src' / 'trcc' / 'gui' / 'assets'
CODE_DIRS = [
    PROJECT_ROOT / 'src' / 'trcc',
    PROJECT_ROOT / 'tests',
]

# i18n language suffixes used by C# assets (appended before .png).
# Must stay in sync with LEGACY_TO_ISO keys in core/models.py.
I18N_SUFFIXES = ('en', 'tc', 'd', 'e', 'f', 'h', 'p', 'r', 'x')

# ============================================================================
# RENAME MAP — C# base name (without .png) → English name (without .png)
#
# Convention: {panel}_{purpose}
#   - panel = the Python class/file that owns this asset
#   - purpose = what the asset visually represents
#
# Active-state variants (Chinese 'a' suffix) are handled automatically:
#   P主题轮播   → theme_local_carousel
#   P主题轮播a  → theme_local_carousel_active
#
# i18n variants are handled automatically:
#   D0数码屏    → led_bg_segment
#   D0数码屏d   → led_bg_segment_d
#   D0数码屏en  → led_bg_segment_en
#
# Device button images (A1{DEVICE}.png) are NOT in this map — they use
# product names from PmRegistry and are kept as-is.
# ============================================================================

RENAME_MAP: dict[str, str] = {
    # ------------------------------------------------------------------
    # App shell (trcc_app.py — TRCCApp)
    # ------------------------------------------------------------------
    'App_main': 'app_main_bg',
    'App_form': 'app_form_bg',
    'App_about': 'app_about_bg',
    'App_sysinfo': 'app_sysinfo_bg',
    'App_theme_base': 'app_theme_base_bg',
    'App_theme_gallery': 'app_theme_gallery_bg',
    'Alogout默认': 'app_power',
    'Alogout选中': 'app_power_hover',
    'P帮助': 'app_help',
    'P保存主题': 'app_save',
    'P导出': 'app_export',
    'P导入': 'app_import',
    'P本地主题': 'app_tab_local',
    'P本地主题a': 'app_tab_local_active',
    'P云端背景': 'app_tab_cloud',
    'P云端背景a': 'app_tab_cloud_active',
    'P云端主题': 'app_tab_mask',
    'P云端主题a': 'app_tab_mask_active',
    'p云端主题': 'app_tab_mask_lower',  # lowercase variant
    'P主题设置': 'app_tab_settings',
    'P主题设置a': 'app_tab_settings_active',
    'PL0': 'app_brightness_0',
    'PL1': 'app_brightness_1',
    'PL2': 'app_brightness_2',
    'PL3': 'app_brightness_3',
    'P0主题设置': 'app_theme_settings_bg',

    # ------------------------------------------------------------------
    # Sidebar (uc_device.py — UCDevice)
    # ------------------------------------------------------------------
    'A0硬件列表': 'sidebar_bg',
    'A0数据列表': 'sidebar_sysinfo_bg',
    'A0关于': 'sidebar_about_bg',
    'A0启动界面': 'sidebar_splash',
    'A0无设备': 'sidebar_no_device',
    'A1传感器': 'sidebar_sensor',
    'A1传感器a': 'sidebar_sensor_active',
    'A1关于': 'sidebar_about',
    'A1关于a': 'sidebar_about_active',

    # ------------------------------------------------------------------
    # About panel (uc_about.py — UCAbout)
    # ------------------------------------------------------------------
    'A2立即更新': 'about_update',
    'A2_update_overlay': 'about_update_overlay',
    'A2下拉框底图': 'about_dropdown_bg',
    'A2下拉框选择条': 'about_dropdown_select',
    'A2下拉选择框': 'about_dropdown',
    'P点选框': 'shared_checkbox_off',
    'P点选框A': 'shared_checkbox_on',

    # ------------------------------------------------------------------
    # System info panel (uc_system_info.py)
    # ------------------------------------------------------------------
    'A数据选择': 'sysinfo_select',
    'A增加数组': 'sysinfo_add_group',
    'A上一页a': 'sysinfo_prev_page',
    'A下一页a': 'sysinfo_next_page',
    'A自定义': 'sysinfo_custom',
    'A滚动条': 'sysinfo_scrollbar',
    'Acpu': 'sysinfo_cpu',
    'Agpu': 'sysinfo_gpu',
    'Adram': 'sysinfo_dram',
    'Ahdd': 'sysinfo_hdd',
    'Afan': 'sysinfo_fan',
    'Anet': 'sysinfo_net',

    # ------------------------------------------------------------------
    # Settings sub-panels (uc_theme_setting.py + split files)
    # ------------------------------------------------------------------
    'Panel_overlay': 'settings_overlay',
    'Panel_params': 'settings_params',
    'Panel_background': 'settings_background',
    'ucXiTongXianShi1.BackgroundImage': 'settings_overlay_grid_bg',
    'ucXiTongXianShi1_BackgroundImage': 'settings_overlay_grid_bg_alt',
    'ucXiTongXianShiAdd1.BackgroundImage': 'settings_overlay_add_bg',
    'ucXiTongXianShiColor1.BackgroundImage': 'settings_overlay_color_bg',
    'ucXiTongXianShiTable1.BackgroundImage': 'settings_overlay_table_bg',

    # ------------------------------------------------------------------
    # Preview panel (uc_preview.py — UCPreview)
    # ------------------------------------------------------------------
    'P0播放': 'preview_play',
    'P0暂停': 'preview_pause',
    'P0播放器控制': 'preview_player_control_bg',
    'ucBoFangQiKongZhi1.BackgroundImage': 'preview_video_controls_bg',
    'ucBoFangQiKongZhi1_BackgroundImage': 'preview_video_controls_bg_alt',
    'P0预览': 'preview_btn',
    'P0预览a': 'preview_btn_active',
    'P预览动画': 'preview_animation',

    # Preview frames — resolution-specific
    'P预览320X320': 'preview_320x320',
    'P预览320X240': 'preview_320x240',
    'P预览240X320': 'preview_240x320',
    'P预览240X240': 'preview_240x240',
    'P预览480X480': 'preview_480x480',
    'P预览320X86': 'preview_320x86',
    'P预览86X320': 'preview_86x320',
    'P预览480X270': 'preview_480x270',
    'P预览270X480': 'preview_270x480',
    'P预览480X180': 'preview_480x180',
    'P预览180X480': 'preview_180x480',
    'P预览480X160': 'preview_480x160',
    'P预览160X480': 'preview_160x480',
    'P预览480X116': 'preview_480x116',
    'P预览116X480': 'preview_116x480',
    'P预览480X110': 'preview_480x110',
    'P预览110X480': 'preview_110x480',
    'P预览400X240': 'preview_400x240',
    'P预览240X400': 'preview_240x400',
    'P预览400X180': 'preview_400x180',
    'P预览180X400': 'preview_180x400',
    'P预览427X240': 'preview_427x240',
    'P预览240X427': 'preview_240x427',
    'P预览320X320圆': 'preview_320x320_round',
    'P预览360360圆': 'preview_360x360_round',
    'P预览480X480圆': 'preview_480x480_round',

    # Preview round masks
    'P预览圆形遮罩320320圆': 'preview_round_mask_320x320',
    'P预览圆形遮罩360360圆': 'preview_round_mask_360x360',
    'P预览圆形遮罩480480圆': 'preview_round_mask_480x480',

    # Preview popups (large resolution)
    'P0预览弹窗1280X480': 'preview_popup_1280x480',
    'P0预览弹窗1920X440': 'preview_popup_1920x440',
    'P0预览弹窗1920X462': 'preview_popup_1920x462',
    'P0预览弹窗320X960': 'preview_popup_320x960',
    'P0预览弹窗360X800': 'preview_popup_360x800',
    'P0预览弹窗440X1920': 'preview_popup_440x1920',
    'P0预览弹窗462X1920': 'preview_popup_462x1920',
    'P0预览弹窗480X1280': 'preview_popup_480x1280',
    'P0预览弹窗480X800': 'preview_popup_480x800',
    'P0预览弹窗480X854': 'preview_popup_480x854',
    'P0预览弹窗540X960': 'preview_popup_540x960',
    'P0预览弹窗800X360': 'preview_popup_800x360',
    'P0预览弹窗800X480': 'preview_popup_800x480',
    'P0预览弹窗854X480': 'preview_popup_854x480',
    'P0预览弹窗960X320': 'preview_popup_960x320',
    'P0预览弹窗960X540': 'preview_popup_960x540',

    # Resolution indicator thumbnails (320x320 etc)
    'P320320': 'preview_res_320x320',
    'P320240': 'preview_res_320x240',
    'P240320': 'preview_res_240x320',
    'P240240': 'preview_res_240x240',

    # ------------------------------------------------------------------
    # Image cut panel (uc_image_cut.py)
    # ------------------------------------------------------------------
    'P0图片裁减320320': 'image_cut_320x320',
    'P0图片裁减320240': 'image_cut_320x240',
    'P0图片裁减240320': 'image_cut_240x320',
    'P0图片裁减240240': 'image_cut_240x240',
    'P0图片裁减480480': 'image_cut_480x480',
    'P0图片裁减360360': 'image_cut_360x360',
    'P0图片裁减32086': 'image_cut_320x86',
    'P0图片裁减86320': 'image_cut_86x320',
    'P0图片裁减480270': 'image_cut_480x270',
    'P0图片裁减270480': 'image_cut_270x480',
    'P0图片裁减480180': 'image_cut_480x180',
    'P0图片裁减180480': 'image_cut_180x480',
    'P0图片裁减480160': 'image_cut_480x160',
    'P0图片裁减160480': 'image_cut_160x480',
    'P0图片裁减480116': 'image_cut_480x116',
    'P0图片裁减116480': 'image_cut_116x480',
    'P0图片裁减480110': 'image_cut_480x110',
    'P0图片裁减110480': 'image_cut_110x480',
    'P0图片裁减400240': 'image_cut_400x240',
    'P0图片裁减240400': 'image_cut_240x400',
    'P0图片裁减400180': 'image_cut_400x180',
    'P0图片裁减180400': 'image_cut_180x400',
    'P0图片裁减427240': 'image_cut_427x240',
    'P0图片裁减240427': 'image_cut_240x427',
    'P0图片裁减1280480': 'image_cut_1280x480',

    # ------------------------------------------------------------------
    # Video cut panel (uc_video_cut.py)
    # ------------------------------------------------------------------
    'ucVideoCut1.BackgroundImage': 'video_cut_bg',
    'ucVideoCut1_BackgroundImage': 'video_cut_bg_alt',
    'P0裁减320320': 'video_cut_320x320',
    'P0裁减320240': 'video_cut_320x240',
    'P0裁减240320': 'video_cut_240x320',
    'P0裁减240240': 'video_cut_240x240',
    'P0裁减480480': 'video_cut_480x480',
    'P0裁减360360': 'video_cut_360x360',
    'P0裁减32086': 'video_cut_320x86',
    'P0裁减86320': 'video_cut_86x320',
    'P0裁减480270': 'video_cut_480x270',
    'P0裁减270480': 'video_cut_270x480',
    'P0裁减480180': 'video_cut_480x180',
    'P0裁减180480': 'video_cut_180x480',
    'P0裁减480160': 'video_cut_480x160',
    'P0裁减160480': 'video_cut_160x480',
    'P0裁减480116': 'video_cut_480x116',
    'P0裁减116480': 'video_cut_116x480',
    'P0裁减480110': 'video_cut_480x110',
    'P0裁减110480': 'video_cut_110x480',
    'P0裁减400240': 'video_cut_400x240',
    'P0裁减240400': 'video_cut_240x400',
    'P0裁减400180': 'video_cut_400x180',
    'P0裁减180400': 'video_cut_180x400',
    'P0裁减427240': 'video_cut_427x240',
    'P0裁减240427': 'video_cut_240x427',
    'P0裁减1280480': 'video_cut_1280x480',

    # ------------------------------------------------------------------
    # Overlay element (overlay_element.py — OverlayElementWidget)
    # ------------------------------------------------------------------
    'P数据': 'overlay_mode_hardware',
    'P时间': 'overlay_mode_time',
    'P星期': 'overlay_mode_weekday',
    'P日期': 'overlay_mode_date',
    'P文本': 'overlay_mode_text',
    'P选中': 'overlay_select',

    # ------------------------------------------------------------------
    # Overlay grid (overlay_grid.py — OverlayGridPanel)
    # ------------------------------------------------------------------
    # toggle on/off shared with display_mode_panels
    'P滑动开': 'shared_toggle_on',
    'P滑动关': 'shared_toggle_off',

    # ------------------------------------------------------------------
    # Color and add panels (color_and_add_panels.py)
    # ------------------------------------------------------------------
    'P吸管': 'color_panel_eyedropper',
    'P取色': 'color_panel_picker',
    'P颜色滑动块': 'color_panel_slider_thumb',
    'P颜色选择圈': 'color_panel_selector_ring',
    'P加': 'display_mode_plus',
    'P减': 'display_mode_minus',
    'P增加内容': 'settings_add_content_btn',
    'P增加图标': 'settings_add_icon',
    'P增加文本': 'settings_add_text',
    'P增加文本1': 'settings_add_text_1',
    'P增加日期': 'settings_add_date',
    'P增加日期1': 'settings_add_date_1',
    'P增加时间': 'settings_add_time',
    'P增加时间1': 'settings_add_time_1',
    'P增加星期': 'settings_add_weekday',
    'P增加星期1': 'settings_add_weekday_1',
    'P添加链接1': 'settings_add_link_1',

    # ------------------------------------------------------------------
    # Display mode panels (display_mode_panels.py)
    # ------------------------------------------------------------------
    'P单位开关': 'display_mode_unit_c',
    'P单位开关a': 'display_mode_unit_f',
    'P12H': 'display_mode_12h',
    'P24H': 'display_mode_24h',
    'PYMD': 'display_mode_date_ymd',
    'PDMY': 'display_mode_date_dmy',
    'PMD': 'display_mode_date_md',
    'PDM': 'display_mode_date_dm',
    'P图片': 'display_mode_icon_image',
    'P视频': 'display_mode_icon_video',
    'P蒙板': 'display_mode_icon_mask',
    'P动画': 'display_mode_icon_gif',
    'P网络': 'display_mode_icon_network',
    'P直播视频载入': 'display_mode_icon_livestream',
    'P网络按钮': 'display_mode_network_btn',
    'P图标': 'display_mode_icon',
    'PM1': 'display_mode_m1',
    'PM1a': 'display_mode_m1_active',
    'PM2': 'display_mode_m2',
    'PM2a': 'display_mode_m2_active',
    'PM3': 'display_mode_m3',
    'PM3a': 'display_mode_m3_active',
    'PM4': 'display_mode_m4',
    'PM4a': 'display_mode_m4_active',
    'PM5': 'display_mode_m5',
    'PM5a': 'display_mode_m5_active',
    'PM6': 'display_mode_m6',
    'PM6a': 'display_mode_m6_active',
    'P显示边框': 'display_mode_border',
    'P显示边框A': 'display_mode_border_active',
    'P功能选择': 'display_mode_function',
    'P功能选择a': 'display_mode_function_active',
    'P数字字体': 'display_mode_digit_font',
    'P文字字体': 'display_mode_text_font',
    'P宽度适应': 'display_mode_fit_width',
    'P高度适应': 'display_mode_fit_height',
    'P旋转': 'display_mode_rotate',
    'P裁减': 'display_mode_crop',
    'P滑动条按钮': 'display_mode_slider_thumb',

    # ------------------------------------------------------------------
    # Theme local panel (uc_theme_local.py — UCThemeLocal)
    # ------------------------------------------------------------------
    'P主题轮播': 'theme_local_carousel',
    'P主题轮播a': 'theme_local_carousel_active',
    'P导出所有主题': 'theme_local_export_all',
    'P主题分类选择': 'theme_browser_filter',
    'P主题分类选择0': 'theme_browser_filter_active',
    'P多选': 'theme_local_multi_select',
    'P多选a': 'theme_local_multi_select_active',
    'P选择框M': 'theme_local_select_frame',
    'P选择框Ma': 'theme_local_select_frame_active',

    # ------------------------------------------------------------------
    # Theme cloud label (i18n — p0云端背景 etc)
    # ------------------------------------------------------------------
    'p0云端背景': 'theme_cloud_label',
    'p0云端主题': 'theme_mask_label',

    # ------------------------------------------------------------------
    # Carousel / slideshow
    # ------------------------------------------------------------------
    'P轮播': 'carousel_btn',
    'P轮播a': 'carousel_btn_active',
    'P轮播选框': 'carousel_select_frame',
    'P轮播1': 'carousel_1',
    'P轮播2': 'carousel_2',
    'P轮播3': 'carousel_3',
    'P轮播4': 'carousel_4',
    'P轮播5': 'carousel_5',
    'P轮播6': 'carousel_6',

    # ------------------------------------------------------------------
    # Shared UI widgets
    # ------------------------------------------------------------------
    'P关闭按钮': 'shared_close',
    'P关闭按钮2': 'shared_close_2',
    'P圆形关闭': 'shared_round_close',
    'P圆形确定': 'shared_round_confirm',
    'P下拉框': 'shared_dropdown',
    'P下拉框底图2X': 'shared_dropdown_bg_2x',
    'P下拉框底图4X': 'shared_dropdown_bg_4x',
    'P下拉框高亮': 'shared_dropdown_highlight',
    'P亮度条': 'shared_brightness_bar',
    'P进度条': 'shared_progress_bar',
    'P滚动条底图': 'shared_scrollbar_bg',
    'P滚动条按钮': 'shared_scrollbar_thumb',
    'P载入动画': 'shared_loading_animation',
    'P剪辑块a': 'shared_clip_a',
    'P剪辑块b': 'shared_clip_b',
    'P线程单': 'shared_thread_single',
    'P线程多': 'shared_thread_multi',
    'P快捷方式大小滑动条': 'shared_shortcut_size_slider',

    # ------------------------------------------------------------------
    # Sensor picker (uc_sensor_picker.py)
    # ------------------------------------------------------------------
    # Uses shared_checkbox_off/on — no unique assets

    # ------------------------------------------------------------------
    # LED control panel (uc_led_control.py — UCLedControl)
    # ------------------------------------------------------------------
    # LED meter backgrounds/bars
    'P0M1': 'led_meter_bg_1',
    'P0M2': 'led_meter_bg_2',
    'P0M3': 'led_meter_bg_3',
    'P0M4': 'led_meter_bg_4',
    'P0M5': 'led_meter_bg_5',
    'P0M5a': 'led_meter_bg_5_active',
    'P0M6': 'led_meter_bg_6',
    'P环H1': 'led_meter_bar_1',
    'P环H2': 'led_meter_bar_2',
    'P环H3': 'led_meter_bar_3',
    'P环H4': 'led_meter_bar_4',
    'P环H5': 'led_meter_bar_5',
    'P环H6': 'led_meter_bar_6',

    # LED mode buttons (D2灯光{N} — 12 modes)
    'D2灯光1': 'led_mode_1',
    'D2灯光1a': 'led_mode_1_active',
    'D2灯光2': 'led_mode_2',
    'D2灯光2a': 'led_mode_2_active',
    'D2灯光3': 'led_mode_3',
    'D2灯光3a': 'led_mode_3_active',
    'D2灯光4': 'led_mode_4',
    'D2灯光4a': 'led_mode_4_active',
    'D2灯光5': 'led_mode_5',
    'D2灯光5a': 'led_mode_5_active',
    'D2灯光6': 'led_mode_6',
    'D2灯光6a': 'led_mode_6_active',
    'D2灯光7': 'led_mode_7',
    'D2灯光7a': 'led_mode_7_active',
    'D2灯光8': 'led_mode_8',
    'D2灯光8a': 'led_mode_8_active',
    'D2灯光9': 'led_mode_9',
    'D2灯光9a': 'led_mode_9_active',
    'D2灯光10': 'led_mode_10',
    'D2灯光10a': 'led_mode_10_active',
    'D2灯光11': 'led_mode_11',
    'D2灯光11a': 'led_mode_11_active',
    'D2灯光12': 'led_mode_12',
    'D2灯光12a': 'led_mode_12_active',

    # LED zone buttons
    'D4模式1': 'led_zone_mode_1',
    'D4模式1a': 'led_zone_mode_1_active',
    'D4模式2': 'led_zone_mode_2',
    'D4模式2a': 'led_zone_mode_2_active',
    'D4模式3': 'led_zone_mode_3',
    'D4模式3a': 'led_zone_mode_3_active',
    'D4模式4': 'led_zone_mode_4',
    'D4模式4a': 'led_zone_mode_4_active',
    'D4模式5': 'led_zone_mode_5',
    'D4模式5a': 'led_zone_mode_5_active',
    'D4模式6': 'led_zone_mode_6',
    'D4模式6a': 'led_zone_mode_6_active',
    'D4按钮1': 'led_zone_btn_1',
    'D4按钮1a': 'led_zone_btn_1_active',
    'D4按钮2': 'led_zone_btn_2',
    'D4按钮2a': 'led_zone_btn_2_active',
    'D4按钮3': 'led_zone_btn_3',
    'D4按钮3a': 'led_zone_btn_3_active',
    'D4按钮4': 'led_zone_btn_4',
    'D4按钮4a': 'led_zone_btn_4_active',

    # LED power button
    'D4开机': 'led_power',
    'D4开机a': 'led_power_active',

    # LED preset colors
    'D3红': 'led_preset_red',
    'D3橙': 'led_preset_orange',
    'D3黄': 'led_preset_yellow',
    'D3绿': 'led_preset_green',
    'D3湖': 'led_preset_cyan',
    'D3蓝': 'led_preset_blue',
    'D3紫': 'led_preset_purple',
    'D3白': 'led_preset_white',

    # LED light show buttons
    'D3灯光秀1': 'led_show_1',
    'D3灯光秀1a': 'led_show_1_active',
    'D3灯光秀2': 'led_show_2',
    'D3灯光秀2a': 'led_show_2_active',
    'D3灯光秀3': 'led_show_3',
    'D3灯光秀3a': 'led_show_3_active',

    # LED slider
    'D3滑动条按钮': 'led_slider_thumb',

    # LED aggregate / external output
    'D1灯光聚合': 'led_aggregate',
    'D1灯光聚合a': 'led_aggregate_active',
    'D1外部输出': 'led_external',
    'D1外部输出a': 'led_external_active',

    # LED helmet icons
    'D1头盔1': 'led_helmet_1',
    'D1头盔2': 'led_helmet_2',
    'D1头盔3': 'led_helmet_3',
    'D1头盔4': 'led_helmet_4',
    'D1头盔5': 'led_helmet_5',

    # LED dropdowns
    'D下拉框': 'led_dropdown',
    'D下拉框2': 'led_dropdown_2',
    'D下拉菜单': 'led_dropdown_menu',
    'D下拉菜单2': 'led_dropdown_menu_2',
    'D下拉高亮': 'led_dropdown_highlight',
    'D下拉高亮2': 'led_dropdown_highlight_2',

    # LED KVMA
    'D0KVMA灯控': 'led_bg_kvma',

    # ------------------------------------------------------------------
    # LED panel backgrounds (i18n — D0{DEVICE})
    # ------------------------------------------------------------------
    'D0数码屏': 'led_bg_segment',
    'D0数码屏4区域': 'led_bg_segment_4zone',
    'D0CZ1': 'led_bg_cz1',
    'D0LC1': 'led_bg_lc1',
    'D0LC2': 'led_bg_lc2',
    'D0LF8': 'led_bg_lf8',
    'D0LF10': 'led_bg_lf10',
    'D0LF11': 'led_bg_lf11',
    'D0LF12': 'led_bg_lf12',
    'D0LF13': 'led_bg_lf13',
    'D0LF15': 'led_bg_lf15',
    'D0rgblf13': 'led_bg_rgb_lf13',

    # LED device previews (D{DEVICE})
    'DAX120_DIGITAL': 'led_preview_ax120',
    'DAK120_DIGITAL': 'led_preview_ak120',
    'DPA120_DIGITAL': 'led_preview_pa120',
    'DPA120 DIGITAL': 'led_preview_pa120_space',
    'DCZ1': 'led_preview_cz1',
    'DLC1': 'led_preview_lc1',
    'DLC2': 'led_preview_lc2',
    'DLF8': 'led_preview_lf8',
    'DLF10': 'led_preview_lf10',
    'DLF11': 'led_preview_lf11',
    'DLF12': 'led_preview_lf12',
    'DLF13': 'led_preview_lf13',
    'DLF15': 'led_preview_lf15',
    'DFROZEN_HORIZON_PRO': 'led_preview_frozen_horizon_pro',
    'DFROZEN_MAGIC_PRO': 'led_preview_frozen_magic_pro',

    # ------------------------------------------------------------------
    # Color wheel (uc_color_wheel.py)
    # ------------------------------------------------------------------
    'D3旋钮': 'color_wheel_knob',
    'D3旋钮0': 'color_wheel_knob_0',
    'D3开关': 'color_wheel_toggle_off',
    'D3开关a': 'color_wheel_toggle_on',
    'D3开关b': 'color_wheel_toggle_hover',

    # ------------------------------------------------------------------
    # Screen LED decoration (uc_screen_led.py)
    # ------------------------------------------------------------------
    'Dch1': 'screen_led_deco_1',
    'Dch2': 'screen_led_deco_2',
    'Dch3': 'screen_led_deco_3',
    'Dch4': 'screen_led_deco_4',
    'Dchcz1': 'screen_led_deco_cz1',

    # ------------------------------------------------------------------
    # Split overlay / dynamic island (models.py SPLIT_OVERLAY_MAP)
    # ------------------------------------------------------------------
    'P灵动岛': 'split_overlay_a',
    'P灵动岛90': 'split_overlay_a_90',
    'P灵动岛180': 'split_overlay_a_180',
    'P灵动岛270': 'split_overlay_a_270',
    'P灵动岛a': 'split_overlay_b',
    'P灵动岛a90': 'split_overlay_b_90',
    'P灵动岛a180': 'split_overlay_b_180',
    'P灵动岛a270': 'split_overlay_b_270',
    'P灵动岛b': 'split_overlay_c',
    'P灵动岛b90': 'split_overlay_c_90',
    'P灵动岛b180': 'split_overlay_c_180',
    'P灵动岛b270': 'split_overlay_c_270',

    # ------------------------------------------------------------------
    # Secondary screen (F0副屏 — i18n)
    # ------------------------------------------------------------------
    'F0副屏': 'secondary_screen_bg',

    # ------------------------------------------------------------------
    # Shortcut / overlay label panels (i18n — P01*)
    # ------------------------------------------------------------------
    'P01快捷方式': 'shortcuts_panel',
    'P01系统信息': 'overlay_label_sysinfo',
    'P01时间显示': 'overlay_label_time',
    'P01自定文字': 'overlay_label_text',
    'P01模块设置': 'settings_module',
    'P01亮度调整': 'settings_brightness',
    'P01增加内容': 'settings_add_content',
    'P01增加内容遮罩': 'settings_add_content_mask',
    'P01动画联动': 'settings_anim_sync',
    'P01键盘联动1': 'settings_keyboard_sync_1',
    'P01键盘联动2': 'settings_keyboard_sync_2',
    'P01键盘联动3': 'settings_keyboard_sync_3',
    'P1系统信息en': 'overlay_label_sysinfo_1_en',
    'P1系统信息tc': 'overlay_label_sysinfo_1_tc',

    # ------------------------------------------------------------------
    # Special / misc
    # ------------------------------------------------------------------
    'FROZEN_WARFRAME_Ultra': 'frozen_warframe_ultra',
    'FROZEN_WARFRAME_Ultraa': 'frozen_warframe_ultra_active',
}

# i18n base names — these have language-suffixed variants that should be
# renamed following the same pattern with the suffix appended.
# e.g. D0数码屏 → led_bg_segment, D0数码屏d → led_bg_segment_d
I18N_BASES: set[str] = {
    'D0数码屏', 'D0数码屏4区域',
    'D0CZ1', 'D0LC1', 'D0LC2',
    'D0LF8', 'D0LF10', 'D0LF11', 'D0LF12', 'D0LF13', 'D0LF15',
    'F0副屏',
    'P01快捷方式',
    'P01系统信息', 'P01时间显示', 'P01自定文字',
    'p0云端背景',
}


def _build_full_map() -> dict[str, str]:
    """Expand RENAME_MAP with i18n variants for I18N_BASES."""
    full = dict(RENAME_MAP)
    for base in I18N_BASES:
        if base not in RENAME_MAP:
            continue
        eng = RENAME_MAP[base]
        for suffix in I18N_SUFFIXES:
            old = f"{base}{suffix}"
            if old not in full:
                full[old] = f"{eng}_{suffix}"
    return full


def _find_asset_files(assets_dir: Path) -> dict[str, Path]:
    """Map base name (no extension) → file path for all .png files."""
    result: dict[str, Path] = {}
    for f in assets_dir.iterdir():
        if f.suffix.lower() == '.png':
            result[f.stem] = f
    return result


def _rename_files(full_map: dict[str, str], assets_dir: Path,
                  dry_run: bool) -> list[tuple[str, str]]:
    """Rename asset files. Returns list of (old_name, new_name) pairs."""
    files = _find_asset_files(assets_dir)
    renames: list[tuple[str, str]] = []

    for old_base, new_base in sorted(full_map.items()):
        if old_base in files:
            old_path = files[old_base]
            new_path = old_path.parent / f"{new_base}{old_path.suffix}"
            if old_path == new_path:
                continue
            if new_path.exists():
                print(f"  SKIP (target exists): {old_path.name} → {new_path.name}")
                continue
            renames.append((old_path.name, new_path.name))
            if dry_run:
                print(f"  WOULD RENAME: {old_path.name} → {new_path.name}")
            else:
                old_path.rename(new_path)
                print(f"  RENAMED: {old_path.name} → {new_path.name}")

    return renames


def _build_code_replacements(full_map: dict[str, str]) -> list[tuple[str, str]]:
    """Build (old_string, new_string) pairs for code updates.

    Sorted longest-first to avoid partial replacements (e.g. replacing
    'P主题设置' before 'P0主题设置').
    """
    pairs: list[tuple[str, str]] = []
    for old_base, new_base in full_map.items():
        # With .png extension
        pairs.append((f"{old_base}.png", f"{new_base}.png"))
        # Without extension (bare name references)
        pairs.append((old_base, new_base))

    # Sort by old string length descending to avoid partial replacements
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def _update_code_files(replacements: list[tuple[str, str]],
                       code_dirs: list[Path], dry_run: bool) -> int:
    """Replace old asset names in Python source files. Returns files changed."""
    changed = 0

    for code_dir in code_dirs:
        for py_file in sorted(code_dir.rglob('*.py')):
            content = py_file.read_text(encoding='utf-8')
            new_content = content

            for old, new in replacements:
                new_content = new_content.replace(old, new)

            if new_content != content:
                changed += 1
                rel = py_file.relative_to(PROJECT_ROOT)
                if dry_run:
                    # Show which replacements apply
                    for old, new in replacements:
                        if old in content:
                            print(f"  WOULD REPLACE in {rel}: '{old}' → '{new}'")
                else:
                    py_file.write_text(new_content, encoding='utf-8')
                    print(f"  UPDATED: {rel}")

    return changed


def _report_unmapped(full_map: dict[str, str], assets_dir: Path) -> list[str]:
    """Find asset files not covered by the rename map."""
    files = _find_asset_files(assets_dir)
    mapped_old = set(full_map.keys())
    # Also consider already-renamed files (values in the map)
    mapped_new = set(full_map.values())

    unmapped = []
    for base_name in sorted(files.keys()):
        # Skip device button images (A1{DEVICE})
        if base_name.startswith('A1') and base_name not in (
            'A1传感器', 'A1传感器a', 'A1关于', 'A1关于a',
        ):
            continue
        if base_name not in mapped_old and base_name not in mapped_new:
            unmapped.append(base_name)

    return unmapped


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Rename C# Chinese GUI assets to English panel-based names.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without modifying files')
    parser.add_argument('--unmapped', action='store_true',
                        help='Only report unmapped asset files')
    parser.add_argument('--files-only', action='store_true',
                        help='Only rename files, skip code updates')
    parser.add_argument('--code-only', action='store_true',
                        help='Only update code, skip file renames')
    args = parser.parse_args()

    full_map = _build_full_map()

    if args.unmapped:
        unmapped = _report_unmapped(full_map, ASSETS_DIR)
        if unmapped:
            print(f"\n{len(unmapped)} unmapped assets:")
            for name in unmapped:
                print(f"  {name}.png")
        else:
            print("All assets are mapped.")
        return

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"\n=== Asset Rename Tool ({mode}) ===\n")
    print(f"Map entries: {len(RENAME_MAP)} base + {len(full_map) - len(RENAME_MAP)} i18n = {len(full_map)} total")

    if not args.code_only:
        print(f"\n--- File Renames ({ASSETS_DIR.relative_to(PROJECT_ROOT)}) ---")
        renames = _rename_files(full_map, ASSETS_DIR, args.dry_run)
        print(f"\nFiles: {len(renames)} renamed")

    if not args.files_only:
        print("\n--- Code Updates ---")
        replacements = _build_code_replacements(full_map)
        changed = _update_code_files(replacements, CODE_DIRS, args.dry_run)
        print(f"\nCode files: {changed} updated")

    # Always report unmapped at the end
    unmapped = _report_unmapped(full_map, ASSETS_DIR)
    if unmapped:
        print(f"\n⚠  {len(unmapped)} unmapped assets (new from C# update?):")
        for name in unmapped[:20]:
            print(f"  {name}.png")
        if len(unmapped) > 20:
            print(f"  ... and {len(unmapped) - 20} more")
    else:
        print("\n✓ All assets mapped")


if __name__ == '__main__':
    main()
