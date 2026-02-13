"""Shared constants for TRCC Linux.

Format modes matching Windows TRCC (UCXiTongXianShiSub.cs).
"""

# Time formats:
#   case 0: DateTime.Now.ToString("HH:mm")
#   case 1: DateTime.Now.ToString("hh:mm tt", CultureInfo.InvariantCulture)
#   case 2: DateTime.Now.ToString("HH:mm")  -- same as case 0
TIME_FORMATS = {
    0: "%H:%M",       # 24-hour (14:58)
    1: "%-I:%M %p",   # 12-hour with AM/PM, no leading zero (2:58 PM)
    2: "%H:%M",       # 24-hour (same as mode 0 in Windows)
}

# Date formats:
#   case 0, 1: DateTime.Now.ToString("yyyy/MM/dd")
#   case 2: DateTime.Now.ToString("dd/MM/yyyy")
#   case 3: DateTime.Now.ToString("MM/dd")
#   case 4: DateTime.Now.ToString("dd/MM")
DATE_FORMATS = {
    0: "%Y/%m/%d",    # 2026/01/30
    1: "%Y/%m/%d",    # 2026/01/30 (same as mode 0 in Windows)
    2: "%d/%m/%Y",    # 30/01/2026
    3: "%m/%d",       # 01/30
    4: "%d/%m",       # 30/01
}

# Weekday names matching Windows TRCC (English)
# Windows DayOfWeek: Sunday=0, Saturday=6
# Python weekday(): Monday=0, Sunday=6
# Array adapted for Python's weekday() numbering
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

# Chinese weekday names (for Language == 1)
WEEKDAYS_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
