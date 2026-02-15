#!/usr/bin/env python3
"""
LED segment visualization widget (UCScreenLED equivalent).

Matches Windows UCScreenLED.cs rendering exactly:
- 460x460 widget with exact ledPosition rectangle coordinates per style
- Paint order: dark fill → decorations → LED colored rectangles → device overlay LAST
- The device image (e.g. DAX120_DIGITAL.png) is a foreground mask drawn on top;
  LED colors show through its transparent areas.
- Style-specific decoration images (Dch1-4, Dchcz1, D0rgblf13) for styles 6/7/8/12.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .assets import load_pixmap

# =========================================================================
# LED position arrays — exact coordinates from UCScreenLED.cs
# Each entry is (x, y, width, height) matching Rectangle(x, y, w, h).
# =========================================================================

# Style 1: AX120_DIGITAL (30 LEDs)
_POS_1: tuple[tuple[int, int, int, int], ...] = (
    (207, 53, 23, 46), (230, 53, 23, 46),
    (105, 123, 29, 24), (134, 123, 29, 24),
    (257, 123, 29, 24), (286, 123, 29, 24),
    (360, 163, 25, 26), (360, 222, 25, 26), (360, 284, 25, 26),
    (80, 163, 68, 12), (141, 170, 12, 62), (141, 241, 12, 62),
    (80, 298, 68, 12), (75, 241, 12, 62), (75, 170, 12, 62),
    (84, 229, 60, 15),
    (176, 163, 68, 12), (237, 170, 12, 62), (237, 241, 12, 62),
    (178, 298, 68, 12), (171, 241, 12, 62), (171, 170, 12, 62),
    (180, 229, 60, 15),
    (272, 163, 68, 12), (333, 170, 12, 62), (333, 241, 12, 62),
    (272, 298, 68, 12), (267, 241, 12, 62), (267, 170, 12, 62),
    (276, 229, 60, 15),
)

# Style 2: PA120_DIGITAL (84 LEDs)
_POS_2: tuple[tuple[int, int, int, int], ...] = (
    (49, 63, 39, 25), (88, 63, 39, 25),
    (49, 245, 39, 25), (88, 245, 39, 25),
    (236, 108, 19, 22), (231, 167, 20, 22),
    (399, 167, 21, 22), (236, 290, 19, 22),
    (231, 349, 20, 22), (399, 349, 21, 22),
    (50, 109, 37, 9), (81, 116, 12, 29), (77, 155, 11, 30),
    (44, 182, 37, 9), (40, 155, 11, 29), (42, 115, 12, 30),
    (49, 145, 34, 9),
    (112, 109, 37, 9), (143, 116, 12, 29), (139, 155, 11, 30),
    (106, 182, 37, 9), (102, 155, 11, 29), (104, 115, 12, 30),
    (111, 145, 34, 9),
    (174, 109, 37, 9), (205, 116, 12, 29), (201, 155, 11, 30),
    (168, 182, 37, 9), (164, 155, 11, 29), (166, 115, 12, 30),
    (173, 145, 34, 9),
    (303, 126, 29, 7), (328, 131, 9, 23), (325, 161, 9, 23),
    (298, 181, 29, 7), (294, 161, 9, 22), (296, 130, 10, 23),
    (302, 153, 27, 7),
    (353, 126, 29, 7), (378, 131, 9, 23), (375, 161, 9, 23),
    (348, 181, 29, 7), (344, 161, 9, 22), (346, 130, 10, 23),
    (352, 153, 27, 7),
    (50, 291, 37, 9), (81, 298, 12, 29), (77, 337, 11, 30),
    (44, 364, 37, 9), (40, 337, 11, 29), (42, 297, 12, 30),
    (49, 327, 34, 9),
    (112, 291, 37, 9), (143, 298, 12, 29), (139, 337, 11, 30),
    (106, 364, 37, 9), (102, 337, 11, 29), (104, 297, 12, 30),
    (111, 327, 34, 9),
    (174, 291, 37, 9), (205, 298, 12, 29), (201, 337, 11, 30),
    (168, 364, 37, 9), (164, 337, 11, 29), (166, 297, 12, 30),
    (173, 327, 34, 9),
    (303, 308, 29, 7), (328, 313, 9, 23), (325, 343, 9, 23),
    (298, 363, 29, 7), (294, 343, 9, 22), (296, 312, 10, 23),
    (302, 335, 27, 7),
    (353, 308, 29, 7), (378, 313, 9, 23), (375, 343, 9, 23),
    (348, 363, 29, 7), (344, 343, 9, 22), (346, 312, 10, 23),
    (352, 335, 27, 7),
    (278, 131, 9, 23), (275, 161, 9, 23),
    (278, 313, 9, 23), (275, 343, 9, 23),
)

# Style 3: AK120_DIGITAL (64 LEDs)
_POS_3: tuple[tuple[int, int, int, int], ...] = (
    (202, 34, 56, 20),
    (312, 127, 29, 29), (312, 190, 29, 29),
    (312, 241, 29, 29), (312, 355, 29, 29),
    (202, 405, 56, 20),
    (124, 69, 43, 8), (163, 73, 8, 40), (163, 119, 8, 40),
    (124, 155, 43, 8), (120, 119, 8, 40), (120, 73, 8, 40),
    (126, 111, 39, 10),
    (186, 69, 43, 8), (225, 73, 8, 40), (225, 119, 8, 40),
    (186, 155, 43, 8), (182, 119, 8, 40), (182, 73, 8, 40),
    (188, 111, 39, 10),
    (248, 69, 43, 8), (287, 73, 8, 40), (287, 119, 8, 40),
    (248, 155, 43, 8), (244, 119, 8, 40), (244, 73, 8, 40),
    (250, 111, 39, 10),
    (124, 183, 43, 8), (163, 187, 8, 40), (163, 233, 8, 40),
    (124, 269, 43, 8), (120, 233, 8, 40), (120, 187, 8, 40),
    (126, 225, 39, 10),
    (186, 183, 43, 8), (225, 187, 8, 40), (225, 233, 8, 40),
    (186, 269, 43, 8), (182, 233, 8, 40), (182, 187, 8, 40),
    (188, 225, 39, 10),
    (248, 183, 43, 8), (287, 187, 8, 40), (287, 233, 8, 40),
    (248, 269, 43, 8), (244, 233, 8, 40), (244, 187, 8, 40),
    (250, 225, 39, 10),
    (186, 297, 43, 8), (225, 301, 8, 40), (225, 347, 8, 40),
    (186, 383, 43, 8), (182, 347, 8, 40), (182, 301, 8, 40),
    (188, 339, 39, 10),
    (248, 297, 43, 8), (287, 301, 8, 40), (287, 347, 8, 40),
    (248, 383, 43, 8), (244, 347, 8, 40), (244, 301, 8, 40),
    (250, 339, 39, 10),
    (163, 301, 8, 40), (163, 347, 8, 40),
)

# Style 4: LC1 (31 LEDs)
_POS_4: tuple[tuple[int, int, int, int], ...] = (
    (277, 162, 12, 12),
    (370, 190, 26, 27), (370, 243, 26, 27),
    (73, 179, 40, 8), (113, 187, 8, 36), (113, 237, 8, 36),
    (73, 273, 40, 8), (65, 237, 8, 36), (65, 187, 8, 36),
    (73, 226, 40, 8),
    (149, 179, 40, 8), (189, 187, 8, 36), (189, 237, 8, 36),
    (149, 273, 40, 8), (141, 237, 8, 36), (141, 187, 8, 36),
    (149, 226, 40, 8),
    (225, 179, 40, 8), (265, 187, 8, 36), (265, 237, 8, 36),
    (225, 273, 40, 8), (217, 237, 8, 36), (217, 187, 8, 36),
    (225, 226, 40, 8),
    (301, 179, 40, 8), (341, 187, 8, 36), (341, 237, 8, 36),
    (301, 273, 40, 8), (293, 237, 8, 36), (293, 187, 8, 36),
    (301, 226, 40, 8),
)

# Style 5: LF8 (93 LEDs)
_POS_5: tuple[tuple[int, int, int, int], ...] = (
    (43, 98, 46, 20), (99, 98, 46, 20),
    (211, 136, 27, 27), (213, 179, 27, 27),
    (404, 189, 22, 18), (249, 330, 42, 26), (408, 335, 22, 22),
    (49, 133, 34, 6), (81, 137, 6, 32), (81, 173, 6, 32),
    (49, 203, 34, 6), (45, 173, 6, 32), (45, 137, 6, 32),
    (49, 167, 34, 8),
    (105, 133, 34, 6), (137, 137, 6, 32), (137, 173, 6, 32),
    (105, 203, 34, 6), (101, 173, 6, 32), (101, 137, 6, 32),
    (105, 167, 34, 8),
    (161, 133, 34, 6), (193, 137, 6, 32), (193, 173, 6, 32),
    (161, 203, 34, 6), (157, 173, 6, 32), (157, 137, 6, 32),
    (161, 167, 34, 8),
    (274, 152, 27, 4), (300, 155, 4, 24), (300, 182, 4, 24),
    (274, 205, 27, 4), (271, 182, 4, 24), (271, 155, 4, 24),
    (274, 178, 27, 5),
    (317, 152, 27, 4), (343, 155, 4, 24), (343, 182, 4, 24),
    (317, 205, 27, 4), (314, 182, 4, 24), (314, 155, 4, 24),
    (317, 178, 27, 5),
    (360, 152, 27, 4), (386, 155, 4, 24), (386, 182, 4, 24),
    (360, 205, 27, 4), (357, 182, 4, 24), (357, 155, 4, 24),
    (360, 178, 27, 5),
    (30, 283, 34, 6), (62, 287, 6, 32), (62, 323, 6, 32),
    (30, 353, 34, 6), (26, 323, 6, 32), (26, 287, 6, 32),
    (30, 317, 34, 8),
    (86, 283, 34, 6), (118, 287, 6, 32), (118, 323, 6, 32),
    (86, 353, 34, 6), (82, 323, 6, 32), (82, 287, 6, 32),
    (86, 317, 34, 8),
    (142, 283, 34, 6), (174, 287, 6, 32), (174, 323, 6, 32),
    (142, 353, 34, 6), (138, 323, 6, 32), (138, 287, 6, 32),
    (142, 317, 34, 8),
    (197, 283, 34, 6), (229, 287, 6, 32), (229, 323, 6, 32),
    (197, 353, 34, 6), (193, 323, 6, 32), (193, 287, 6, 32),
    (197, 317, 34, 8),
    (321, 302, 27, 4), (347, 305, 4, 24), (347, 332, 4, 24),
    (321, 355, 27, 4), (318, 332, 4, 24), (318, 305, 4, 24),
    (321, 328, 27, 5),
    (364, 302, 27, 4), (390, 305, 4, 24), (390, 332, 4, 24),
    (364, 355, 27, 4), (361, 332, 4, 24), (361, 305, 4, 24),
    (364, 328, 27, 5),
    (304, 305, 4, 24), (304, 332, 4, 24),
)

# Style 6: LF12 (93 LEDs — same count as LF8, different positions)
_POS_6: tuple[tuple[int, int, int, int], ...] = (
    (106, 121, 36, 18), (183, 121, 36, 18),
    (228, 155, 18, 17), (228, 183, 18, 17),
    (363, 190, 10, 11), (246, 313, 32, 18), (376, 323, 8, 11),
    (110, 152, 24, 4), (133, 155, 4, 22), (133, 179, 4, 22),
    (110, 200, 24, 4), (107, 179, 4, 22), (107, 155, 4, 22),
    (110, 176, 24, 4),
    (150, 152, 24, 4), (173, 155, 4, 22), (173, 179, 4, 22),
    (150, 200, 24, 4), (147, 179, 4, 22), (147, 155, 4, 22),
    (150, 176, 24, 4),
    (190, 152, 24, 4), (213, 155, 4, 22), (213, 179, 4, 22),
    (190, 200, 24, 4), (187, 179, 4, 22), (187, 155, 4, 22),
    (190, 176, 24, 4),
    (270, 163, 19, 3), (288, 165, 3, 17), (288, 185, 3, 17),
    (270, 201, 19, 3), (268, 185, 3, 17), (268, 165, 3, 17),
    (269, 182, 21, 3),
    (301, 163, 19, 3), (319, 165, 3, 17), (319, 185, 3, 17),
    (301, 201, 19, 3), (299, 185, 3, 17), (299, 165, 3, 17),
    (300, 182, 21, 3),
    (332, 163, 19, 3), (350, 165, 3, 17), (350, 185, 3, 17),
    (332, 201, 19, 3), (330, 185, 3, 17), (330, 165, 3, 17),
    (331, 182, 21, 3),
    (88, 282, 24, 4), (111, 285, 4, 22), (111, 309, 4, 22),
    (88, 330, 24, 4), (85, 309, 4, 22), (85, 285, 4, 22),
    (88, 306, 24, 4),
    (128, 282, 24, 4), (151, 285, 4, 22), (151, 309, 4, 22),
    (128, 330, 24, 4), (125, 309, 4, 22), (125, 285, 4, 22),
    (128, 306, 24, 4),
    (168, 282, 24, 4), (191, 285, 4, 22), (191, 309, 4, 22),
    (168, 330, 24, 4), (165, 309, 4, 22), (165, 285, 4, 22),
    (168, 306, 24, 4),
    (208, 282, 24, 4), (231, 285, 4, 22), (231, 309, 4, 22),
    (208, 330, 24, 4), (205, 309, 4, 22), (205, 285, 4, 22),
    (208, 306, 24, 4),
    (312, 293, 19, 3), (330, 295, 3, 17), (330, 315, 3, 17),
    (312, 331, 19, 3), (310, 315, 3, 17), (310, 295, 3, 17),
    (311, 312, 21, 3),
    (343, 293, 19, 3), (361, 295, 3, 17), (361, 315, 3, 17),
    (343, 331, 19, 3), (341, 315, 3, 17), (341, 295, 3, 17),
    (342, 312, 21, 3),
    (299, 295, 3, 17), (299, 315, 3, 17),
)

# Style 7: LF10 (104 LEDs — includes bottom strip segments)
_POS_7: tuple[tuple[int, int, int, int], ...] = (
    (50, 279, 42, 15), (167, 310, 22, 28),
    (167, 342, 22, 28), (276, 279, 42, 15),
    (393, 310, 22, 28), (393, 342, 22, 28),
    (45, 318, 5, 5), (52, 318, 19, 5), (73, 318, 5, 5),
    (73, 325, 5, 12), (73, 339, 5, 5), (73, 346, 5, 12),
    (73, 360, 5, 5), (52, 360, 19, 5), (45, 360, 5, 5),
    (45, 346, 5, 12), (45, 339, 5, 5), (45, 325, 5, 12),
    (52, 339, 19, 5),
    (85, 318, 5, 5), (92, 318, 19, 5), (113, 318, 5, 5),
    (113, 325, 5, 12), (113, 339, 5, 5), (113, 346, 5, 12),
    (113, 360, 5, 5), (92, 360, 19, 5), (85, 360, 5, 5),
    (85, 346, 5, 12), (85, 339, 5, 5), (85, 325, 5, 12),
    (92, 339, 19, 5),
    (125, 318, 5, 5), (132, 318, 19, 5), (153, 318, 5, 5),
    (153, 325, 5, 12), (153, 339, 5, 5), (153, 346, 5, 12),
    (153, 360, 5, 5), (132, 360, 19, 5), (125, 360, 5, 5),
    (125, 346, 5, 12), (125, 339, 5, 5), (125, 325, 5, 12),
    (132, 339, 19, 5),
    (271, 318, 5, 5), (278, 318, 19, 5), (299, 318, 5, 5),
    (299, 325, 5, 12), (299, 339, 5, 5), (299, 346, 5, 12),
    (299, 360, 5, 5), (278, 360, 19, 5), (271, 360, 5, 5),
    (271, 346, 5, 12), (271, 339, 5, 5), (271, 325, 5, 12),
    (278, 339, 19, 5),
    (311, 318, 5, 5), (318, 318, 19, 5), (339, 318, 5, 5),
    (339, 325, 5, 12), (339, 339, 5, 5), (339, 346, 5, 12),
    (339, 360, 5, 5), (318, 360, 19, 5), (311, 360, 5, 5),
    (311, 346, 5, 12), (311, 339, 5, 5), (311, 325, 5, 12),
    (318, 339, 19, 5),
    (351, 318, 5, 5), (358, 318, 19, 5), (379, 318, 5, 5),
    (379, 325, 5, 12), (379, 339, 5, 5), (379, 346, 5, 12),
    (379, 360, 5, 5), (358, 360, 19, 5), (351, 360, 5, 5),
    (351, 346, 5, 12), (351, 339, 5, 5), (351, 325, 5, 12),
    (358, 339, 19, 5),
    (48, 392, 8, 21), (61, 392, 8, 21), (74, 392, 8, 21),
    (87, 392, 8, 21), (100, 392, 8, 21), (113, 392, 8, 21),
    (126, 392, 8, 21), (139, 392, 8, 21), (152, 392, 8, 21),
    (165, 392, 8, 21),
    (274, 392, 8, 21), (287, 392, 8, 21), (300, 392, 8, 21),
    (313, 392, 8, 21), (326, 392, 8, 21), (339, 392, 8, 21),
    (352, 392, 8, 21), (365, 392, 8, 21), (378, 392, 8, 21),
    (391, 392, 8, 21),
)

# Style 8: CZ1 (18 LEDs — large zones)
_POS_8: tuple[tuple[int, int, int, int], ...] = (
    (73, 114, 73, 16), (313, 114, 73, 16),
    (73, 330, 73, 16), (313, 330, 73, 16),
    (24, 0, 172, 30), (190, 24, 30, 196),
    (190, 240, 30, 196), (24, 430, 172, 30),
    (0, 240, 30, 196), (0, 24, 30, 196),
    (24, 215, 172, 30),
    (264, 0, 172, 30), (430, 24, 30, 196),
    (430, 240, 30, 196), (264, 430, 172, 30),
    (240, 240, 30, 196), (240, 24, 30, 196),
    (264, 215, 172, 30),
)

# Style 9: LC2 (61 LEDs)
_POS_9: tuple[tuple[int, int, int, int], ...] = (
    (223, 122, 14, 14), (223, 166, 14, 14),
    (230, 236, 44, 74),
    (80, 103, 44, 7), (122, 108, 7, 41), (122, 153, 7, 41),
    (80, 192, 44, 7), (75, 153, 7, 41), (75, 108, 7, 41),
    (80, 147, 44, 8),
    (154, 103, 44, 7), (196, 108, 7, 41), (196, 153, 7, 41),
    (154, 192, 44, 7), (149, 153, 7, 41), (149, 108, 7, 41),
    (154, 147, 44, 8),
    (262, 103, 44, 7), (304, 108, 7, 41), (304, 153, 7, 41),
    (262, 192, 44, 7), (257, 153, 7, 41), (257, 108, 7, 41),
    (262, 147, 44, 8),
    (336, 103, 44, 7), (378, 108, 7, 41), (378, 153, 7, 41),
    (336, 192, 44, 7), (331, 153, 7, 41), (331, 108, 7, 41),
    (336, 147, 44, 8),
    (184, 233, 36, 7), (218, 238, 7, 33), (218, 275, 7, 33),
    (184, 306, 36, 7), (179, 275, 7, 33), (179, 238, 7, 33),
    (184, 269, 36, 8),
    (284, 233, 36, 7), (318, 238, 7, 33), (318, 275, 7, 33),
    (284, 306, 36, 7), (279, 275, 7, 33), (279, 238, 7, 33),
    (284, 269, 36, 8),
    (344, 233, 36, 7), (378, 238, 7, 33), (378, 275, 7, 33),
    (344, 306, 36, 7), (339, 275, 7, 33), (339, 238, 7, 33),
    (344, 269, 36, 8),
    (158, 238, 7, 33), (158, 275, 7, 33),
    (54, 343, 40, 14), (106, 343, 40, 14), (158, 343, 40, 14),
    (210, 343, 40, 14), (262, 343, 40, 14), (314, 343, 40, 14),
    (366, 343, 40, 14),
)

# Style 10: LF11 (38 LEDs)
_POS_10: tuple[tuple[int, int, int, int], ...] = (
    (318, 163, 10, 10),
    (410, 190, 26, 27), (410, 243, 26, 27),
    (37, 179, 40, 8), (77, 187, 8, 36), (77, 237, 8, 36),
    (37, 273, 40, 8), (29, 237, 8, 36), (29, 187, 8, 36),
    (37, 226, 40, 8),
    (113, 179, 40, 8), (153, 187, 8, 36), (153, 237, 8, 36),
    (113, 273, 40, 8), (105, 237, 8, 36), (105, 187, 8, 36),
    (113, 226, 40, 8),
    (189, 179, 40, 8), (229, 187, 8, 36), (229, 237, 8, 36),
    (189, 273, 40, 8), (181, 237, 8, 36), (181, 187, 8, 36),
    (189, 226, 40, 8),
    (265, 179, 40, 8), (305, 187, 8, 36), (305, 237, 8, 36),
    (265, 273, 40, 8), (257, 237, 8, 36), (257, 187, 8, 36),
    (265, 226, 40, 8),
    (341, 179, 40, 8), (381, 187, 8, 36), (381, 237, 8, 36),
    (341, 273, 40, 8), (333, 237, 8, 36), (333, 187, 8, 36),
    (341, 226, 40, 8),
)

# Style 11: LF15 (93 LEDs)
_POS_11: tuple[tuple[int, int, int, int], ...] = (
    (84, 131, 36, 18), (84, 151, 36, 18),
    (206, 185, 18, 17), (206, 213, 18, 17),
    (341, 220, 10, 11), (246, 313, 32, 18), (376, 323, 8, 11),
    (88, 182, 24, 4), (111, 185, 4, 22), (111, 209, 4, 22),
    (88, 230, 24, 4), (85, 209, 4, 22), (85, 185, 4, 22),
    (88, 206, 24, 4),
    (128, 182, 24, 4), (151, 185, 4, 22), (151, 209, 4, 22),
    (128, 230, 24, 4), (125, 209, 4, 22), (125, 185, 4, 22),
    (128, 206, 24, 4),
    (168, 182, 24, 4), (191, 185, 4, 22), (191, 209, 4, 22),
    (168, 230, 24, 4), (165, 209, 4, 22), (165, 185, 4, 22),
    (168, 206, 24, 4),
    (248, 193, 19, 3), (266, 195, 3, 17), (266, 215, 3, 17),
    (248, 231, 19, 3), (246, 215, 3, 17), (246, 195, 3, 17),
    (247, 212, 21, 3),
    (279, 193, 19, 3), (297, 195, 3, 17), (297, 215, 3, 17),
    (279, 231, 19, 3), (277, 215, 3, 17), (277, 195, 3, 17),
    (278, 212, 21, 3),
    (310, 193, 19, 3), (328, 195, 3, 17), (328, 215, 3, 17),
    (310, 231, 19, 3), (308, 215, 3, 17), (308, 195, 3, 17),
    (309, 212, 21, 3),
    (88, 282, 24, 4), (111, 285, 4, 22), (111, 309, 4, 22),
    (88, 330, 24, 4), (85, 309, 4, 22), (85, 285, 4, 22),
    (88, 306, 24, 4),
    (128, 282, 24, 4), (151, 285, 4, 22), (151, 309, 4, 22),
    (128, 330, 24, 4), (125, 309, 4, 22), (125, 285, 4, 22),
    (128, 306, 24, 4),
    (168, 282, 24, 4), (191, 285, 4, 22), (191, 309, 4, 22),
    (168, 330, 24, 4), (165, 309, 4, 22), (165, 285, 4, 22),
    (168, 306, 24, 4),
    (208, 282, 24, 4), (231, 285, 4, 22), (231, 309, 4, 22),
    (208, 330, 24, 4), (205, 309, 4, 22), (205, 285, 4, 22),
    (208, 306, 24, 4),
    (312, 293, 19, 3), (330, 295, 3, 17), (330, 315, 3, 17),
    (312, 331, 19, 3), (310, 315, 3, 17), (310, 295, 3, 17),
    (311, 312, 21, 3),
    (343, 293, 19, 3), (361, 295, 3, 17), (361, 315, 3, 17),
    (343, 331, 19, 3), (341, 315, 3, 17), (341, 295, 3, 17),
    (342, 312, 21, 3),
    (299, 295, 3, 17), (299, 315, 3, 17),
)

# Style 12: LF13 (1 full-screen LED)
_POS_12: tuple[tuple[int, int, int, int], ...] = (
    (0, 0, 460, 460),
)

# Style → position array mapping
STYLE_POSITIONS: dict[int, tuple[tuple[int, int, int, int], ...]] = {
    1: _POS_1, 2: _POS_2, 3: _POS_3, 4: _POS_4,
    5: _POS_5, 6: _POS_6, 7: _POS_7, 8: _POS_8,
    9: _POS_9, 10: _POS_10, 11: _POS_11, 12: _POS_12,
}


# =========================================================================
# Decoration config — style-specific overlay images and color fill areas
# from UCScreenLED.cs OnPaint (lines 2895-3042)
# =========================================================================

@dataclass
class _DecoConfig:
    """Decoration rendering config for a specific LED style."""
    images: list[tuple[str, int, int]] = field(default_factory=list)
    color_fills: list[tuple[int, int, int, int]] = field(default_factory=list)


# Decoration image positions and color fill rectangles per style.
# images: (asset_name, x, y) — drawn in display mode (myLedMode == 4)
# color_fills: (x, y, w, h) — filled with LED color[0] in other modes
_DECO: dict[int, _DecoConfig] = {
    # Style 6 (LF12): 3 corner decorations
    6: _DecoConfig(
        images=[("Dch2", 26, 17), ("Dch3", 23, 221), ("Dch4", 293, 274)],
        color_fills=[
            (26, 17, 408, 46),       # Dch2 area
            (23, 221, 414, 28),      # Dch3 area
            (293, 407, 155, 40),     # Dch4 bottom strip (274+173-40=407)
            (408, 274, 40, 173),     # Dch4 right strip  (293+155-40=408)
        ],
    ),
    # Style 7 (LF10): conditional top decoration
    7: _DecoConfig(
        images=[("Dch1", 30, 217)],
        color_fills=[(30, 217, 400, 70), (195, 268, 70, 170)],
    ),
    # Style 8 (CZ1): full background decoration
    8: _DecoConfig(
        images=[("Dchcz1", 0, 0)],
        color_fills=[],
    ),
    # Style 12 (LF13): full screen decoration
    12: _DecoConfig(
        images=[("D0rgblf13", 0, 0)],
        color_fills=[],
    ),
}


class UCScreenLED(QWidget):
    """LED device preview with colored segment rectangles.

    Matches Windows UCScreenLED.cs rendering:
    - 460x460 widget
    - Paint order: dark fill → decorations → LED rectangles → device overlay
    - Device image drawn LAST as foreground mask (LEDs show through transparency)
    """

    segment_clicked = Signal(int)  # segment index

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(460, 460)

        self._style_id = 1
        self._positions = _POS_1
        self._led_count = len(_POS_1)
        self._colors: List[Tuple[int, int, int]] = [(0, 0, 0)] * self._led_count
        self._is_on: List[bool] = [True] * self._led_count
        self._overlay: Optional[QPixmap] = None
        self._led_mode = 0  # 4 = display mode (draws decoration images)
        self._deco_pixmaps: dict[str, QPixmap] = {}

    def set_style(self, style_id: int, segment_count: int) -> None:
        """Configure for a specific LED device style."""
        self._style_id = style_id
        self._positions = STYLE_POSITIONS.get(style_id, _POS_1)
        self._led_count = len(self._positions)
        self._colors = [(0, 0, 0)] * self._led_count
        self._is_on = [True] * self._led_count
        self._load_decorations(style_id)
        self.update()

    def set_overlay(self, pixmap: Optional[QPixmap]) -> None:
        """Set device overlay image (drawn LAST as foreground mask)."""
        self._overlay = pixmap
        self.update()

    # Keep backward compat for callers still using old name
    set_background = set_overlay

    def set_colors(self, colors: List[Tuple[int, int, int]]) -> None:
        """Update LED segment colors from controller tick."""
        self._colors = list(colors[:self._led_count])
        while len(self._colors) < self._led_count:
            self._colors.append((0, 0, 0))
        self.update()

    def set_segment_on(self, index: int, on: bool) -> None:
        """Toggle an individual segment."""
        if 0 <= index < len(self._is_on):
            self._is_on[index] = on
            self.update()

    def set_led_mode(self, mode: int) -> None:
        """Set LED mode (4 = display mode with decoration images)."""
        self._led_mode = mode
        self.update()

    def set_timer(self, month: int, day: int, hour: int, minute: int,
                  day_of_week: int) -> None:
        """Set LC2 clock display data for preview overlay."""
        self._timer_data = (month, day, hour, minute, day_of_week)
        self.update()

    # ================================================================
    # Painting — matches UCScreenLED.cs OnPaint exactly
    # ================================================================

    def paintEvent(self, event: object) -> None:
        """Paint in CS order: dark fill → decorations → LED rects → overlay."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Dark background fill
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        # 2. Style-specific decorations
        self._paint_decorations(painter)

        # 3. LED colored rectangles
        self._paint_leds(painter)

        # 4. Device overlay image LAST (foreground mask)
        if self._overlay:
            painter.drawPixmap(0, 0, self._overlay)

        painter.end()

    def _paint_decorations(self, painter: QPainter) -> None:
        """Draw style-specific decoration images or color fills."""
        deco = _DECO.get(self._style_id)
        if not deco:
            return

        if self._style_id == 8:
            self._paint_deco_cz1(painter, deco)
        elif self._style_id == 12:
            self._paint_deco_lf13(painter, deco)
        elif self._led_mode == 4:
            # Display mode: draw decoration images
            for asset_name, x, y in deco.images:
                pm = self._deco_pixmaps.get(asset_name)
                if pm:
                    painter.drawPixmap(x, y, pm)
        else:
            # Other modes: fill decoration areas with LED color[0]
            if self._colors:
                r, g, b = self._colors[0]
                brush = QBrush(QColor(r, g, b))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(brush)
                for x, y, w, h in deco.color_fills:
                    painter.fillRect(QRect(x, y, w, h), brush)

    def _paint_deco_cz1(self, painter: QPainter, deco: _DecoConfig) -> None:
        """Style 8 (CZ1) decoration: full background, black for off LEDs."""
        if self._led_mode == 4:
            pm = self._deco_pixmaps.get("Dchcz1")
            if pm:
                painter.drawPixmap(0, 0, pm)
            # Fill black for disabled LEDs (inverted logic)
            black = QBrush(QColor(0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            for i, pos in enumerate(self._positions):
                if i < len(self._is_on) and not self._is_on[i]:
                    painter.fillRect(QRect(*pos), black)

    def _paint_deco_lf13(self, painter: QPainter, deco: _DecoConfig) -> None:
        """Style 12 (LF13) decoration: full-screen color or image."""
        if not self._is_on or not self._is_on[0]:
            return
        if self._led_mode == 4:
            pm = self._deco_pixmaps.get("D0rgblf13")
            if pm:
                painter.drawPixmap(0, 0, pm)
        elif self._colors:
            r, g, b = self._colors[0]
            painter.fillRect(QRect(0, 0, 460, 460), QColor(r, g, b))

    def _paint_leds(self, painter: QPainter) -> None:
        """Draw colored rectangles for each enabled LED segment."""
        # Styles 8 and 12 handle their own LED rendering in decorations
        if self._style_id in (8, 12):
            # Style 8: LED rects are the main fill (drawn if on)
            if self._style_id == 8 and self._led_mode != 4:
                self._paint_led_rects(painter)
            return

        self._paint_led_rects(painter)

    def _paint_led_rects(self, painter: QPainter) -> None:
        """Fill colored rectangles at ledPosition coordinates."""
        painter.setPen(Qt.PenStyle.NoPen)
        for i, pos in enumerate(self._positions):
            if i >= len(self._colors):
                break
            is_on = self._is_on[i] if i < len(self._is_on) else True
            if not is_on:
                continue
            r, g, b = self._colors[i]
            painter.fillRect(QRect(*pos), QColor(r, g, b))

    # ================================================================
    # Decoration image loading
    # ================================================================

    def _load_decorations(self, style_id: int) -> None:
        """Pre-load decoration pixmaps for the current style."""
        self._deco_pixmaps.clear()
        deco = _DECO.get(style_id)
        if not deco:
            return
        for asset_name, _x, _y in deco.images:
            pm = load_pixmap(f"{asset_name}.png")
            if pm and not pm.isNull():
                self._deco_pixmaps[asset_name] = pm

    # ================================================================
    # Mouse interaction — rectangle hit-test
    # ================================================================

    def mousePressEvent(self, event: object) -> None:
        """Handle click to toggle segments (rectangle hit-test)."""
        from PySide6.QtGui import QMouseEvent
        if not isinstance(event, QMouseEvent):
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position()
        px, py = pos.x(), pos.y()
        for i, (x, y, w, h) in enumerate(self._positions):
            if x <= px <= x + w and y <= py <= y + h:
                self.segment_clicked.emit(i)
                return
