#!/usr/bin/env python3
"""
System Info Provider for TRCC LCD
Reads CPU/GPU temps, usage, frequencies from hwmon and lm_sensors
"""

import os
import subprocess
import re
from datetime import datetime
from typing import Dict, Optional, Tuple

# Try to import psutil for cross-platform system monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Cache for network I/O delta calculation
_prev_net_io = None
_prev_net_time = None

# Cache for disk I/O delta calculation
_prev_disk_io = None
_prev_disk_time = None


def read_file(path: str) -> Optional[str]:
    """Safely read a file"""
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except:
        return None


def find_hwmon_by_name(name: str) -> Optional[str]:
    """Find hwmon path by sensor name (k10temp, coretemp, amdgpu, etc.)"""
    hwmon_base = "/sys/class/hwmon"
    if not os.path.exists(hwmon_base):
        return None
    
    for i in range(20):
        hwmon_path = f"{hwmon_base}/hwmon{i}"
        name_file = f"{hwmon_path}/name"
        sensor_name = read_file(name_file)
        if sensor_name and name.lower() in sensor_name.lower():
            return hwmon_path
    return None


def get_cpu_temperature() -> Optional[float]:
    """Get CPU temperature from hwmon (k10temp for AMD, coretemp for Intel)"""
    # Try k10temp (AMD)
    hwmon = find_hwmon_by_name("k10temp")
    if not hwmon:
        # Try coretemp (Intel)
        hwmon = find_hwmon_by_name("coretemp")
    
    if hwmon:
        # Try temp1_input first (Tctl on AMD)
        for idx in [1, 2, 3]:
            temp = read_file(f"{hwmon}/temp{idx}_input")
            if temp:
                return float(temp) / 1000.0
    
    # Fallback: try lm_sensors
    try:
        result = subprocess.run(['sensors', '-u'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if 'temp1_input' in line or 'Tctl' in line.lower():
                match = re.search(r':\s*([0-9.]+)', line)
                if match:
                    return float(match.group(1))
    except:
        pass
    
    return None


def get_cpu_usage() -> Optional[float]:
    """Get CPU usage percentage"""
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
            parts = line.split()
            if parts[0] == 'cpu':
                # user, nice, system, idle, iowait, irq, softirq
                user = int(parts[1])
                nice = int(parts[2])
                system = int(parts[3])
                idle = int(parts[4])
                iowait = int(parts[5]) if len(parts) > 5 else 0
                
                total = user + nice + system + idle + iowait
                active = user + nice + system
                
                # Need previous values for delta calculation
                # For simplicity, just return a rough estimate
                return min(100.0, (active / total) * 100) if total > 0 else 0.0
    except:
        pass
    
    # Fallback: use /proc/loadavg
    try:
        loadavg = read_file('/proc/loadavg')
        if loadavg:
            load = float(loadavg.split()[0])
            # Approximate percentage based on load
            return min(100.0, load * 10)
    except:
        pass
    
    return None


def get_cpu_frequency() -> Optional[float]:
    """Get CPU frequency in MHz"""
    # Try cpufreq
    freq = read_file('/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq')
    if freq:
        return float(freq) / 1000.0  # Convert to MHz
    
    # Fallback: /proc/cpuinfo
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if 'cpu MHz' in line:
                    match = re.search(r':\s*([0-9.]+)', line)
                    if match:
                        return float(match.group(1))
    except:
        pass
    
    return None


def get_gpu_temperature() -> Optional[float]:
    """Get GPU temperature (AMD via hwmon, NVIDIA via nvidia-smi)"""
    # AMD GPU (amdgpu)
    hwmon = find_hwmon_by_name("amdgpu")
    if hwmon:
        temp = read_file(f"{hwmon}/temp1_input")
        if temp:
            return float(temp) / 1000.0
    
    # NVIDIA GPU
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=temperature.gpu', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except:
        pass
    
    return None


def get_gpu_usage() -> Optional[float]:
    """Get GPU usage percentage"""
    # AMD GPU
    hwmon = find_hwmon_by_name("amdgpu")
    if hwmon:
        # Try device/gpu_busy_percent
        usage = read_file(f"{hwmon}/device/gpu_busy_percent")
        if usage:
            return float(usage)
    
    # NVIDIA GPU
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except:
        pass
    
    return None


def get_gpu_clock() -> Optional[float]:
    """Get GPU clock in MHz"""
    # AMD GPU
    hwmon = find_hwmon_by_name("amdgpu")
    if hwmon:
        freq = read_file(f"{hwmon}/freq1_input")
        if freq:
            return float(freq) / 1000000.0  # Convert to MHz
    
    # NVIDIA GPU
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=clocks.gr', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except:
        pass
    
    return None


def get_memory_usage() -> Optional[float]:
    """Get memory usage percentage"""
    try:
        with open('/proc/meminfo', 'r') as f:
            meminfo = {}
            for line in f:
                parts = line.split(':')
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = int(parts[1].strip().split()[0])
                    meminfo[key] = value
            
            total = meminfo.get('MemTotal', 0)
            available = meminfo.get('MemAvailable', 0)
            
            if total > 0:
                used = total - available
                return (used / total) * 100
    except:
        pass
    
    return None


def get_memory_available() -> Optional[float]:
    """Get available memory in MB"""
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    # Value is in kB, convert to MB
                    kb = int(line.split()[1])
                    return kb / 1024.0
    except:
        pass
    return None


def get_memory_temperature() -> Optional[float]:
    """Get memory/DIMM temperature from hwmon sensors"""
    # Try hwmon sensors that might expose memory temps
    # Common names: "ddr", "dimm", "memory", some motherboards use chipset sensors
    hwmon_base = "/sys/class/hwmon"
    if os.path.exists(hwmon_base):
        for i in range(20):
            hwmon_path = f"{hwmon_base}/hwmon{i}"
            name_file = f"{hwmon_path}/name"
            sensor_name = read_file(name_file)
            if not sensor_name:
                continue

            # Check for memory-related sensor names
            name_lower = sensor_name.lower()
            if any(x in name_lower for x in ['ddr', 'dimm', 'memory', 'spd']):
                temp = read_file(f"{hwmon_path}/temp1_input")
                if temp:
                    return float(temp) / 1000.0

    # Try lm_sensors output for memory temps
    try:
        result = subprocess.run(['sensors', '-u'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            in_memory_section = False
            for line in result.stdout.split('\n'):
                line_lower = line.lower()
                if any(x in line_lower for x in ['ddr', 'dimm', 'memory']):
                    in_memory_section = True
                elif line and not line.startswith(' ') and ':' not in line:
                    in_memory_section = False
                if in_memory_section and 'temp' in line_lower and '_input' in line_lower:
                    match = re.search(r':\s*([0-9.]+)', line)
                    if match:
                        return float(match.group(1))
    except:
        pass

    return None


def get_memory_clock() -> Optional[float]:
    """Get memory clock speed in MHz (typically requires root)"""
    # Try reading from dmidecode (requires root)
    try:
        result = subprocess.run(
            ['dmidecode', '-t', 'memory'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                # Look for configured speed first, then max speed
                if 'Configured Memory Speed' in line:
                    match = re.search(r'(\d+)\s*(?:MT/s|MHz)', line)
                    if match:
                        return float(match.group(1))
            # Fallback to "Speed:" if configured not found
            for line in result.stdout.split('\n'):
                if 'Speed:' in line and 'Unknown' not in line:
                    match = re.search(r'(\d+)\s*(?:MT/s|MHz)', line)
                    if match:
                        return float(match.group(1))
    except:
        pass

    # Try lshw (also requires root typically)
    try:
        result = subprocess.run(
            ['lshw', '-class', 'memory', '-short'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # Look for speed in the output
            match = re.search(r'(\d+)\s*(?:MT/s|MHz)', result.stdout)
            if match:
                return float(match.group(1))
    except:
        pass

    # Try reading from /sys EDAC (rarely has frequency)
    mc_path = "/sys/devices/system/edac/mc"
    if os.path.exists(mc_path):
        try:
            for mc in os.listdir(mc_path):
                freq_file = f"{mc_path}/{mc}/dimm_info"
                content = read_file(freq_file)
                if content:
                    match = re.search(r'(\d+)\s*MHz', content)
                    if match:
                        return float(match.group(1))
        except:
            pass

    return None


def get_disk_stats() -> Dict[str, float]:
    """Get disk I/O statistics using psutil"""
    global _prev_disk_io, _prev_disk_time

    stats = {}

    if not PSUTIL_AVAILABLE:
        return stats

    try:
        import time
        current_time = time.time()
        current_io = psutil.disk_io_counters()

        if current_io and _prev_disk_io and _prev_disk_time:
            time_delta = current_time - _prev_disk_time
            if time_delta > 0:
                # Calculate read/write rates in MB/s
                read_bytes = current_io.read_bytes - _prev_disk_io.read_bytes
                write_bytes = current_io.write_bytes - _prev_disk_io.write_bytes

                stats['disk_read'] = (read_bytes / time_delta) / (1024 * 1024)  # MB/s
                stats['disk_write'] = (write_bytes / time_delta) / (1024 * 1024)  # MB/s

                # Activity percentage (rough estimate based on busy time if available)
                if hasattr(current_io, 'busy_time') and hasattr(_prev_disk_io, 'busy_time'):
                    busy_delta = current_io.busy_time - _prev_disk_io.busy_time
                    stats['disk_activity'] = min(100.0, (busy_delta / (time_delta * 1000)) * 100)
                else:
                    # Estimate activity from I/O operations
                    total_ops = (read_bytes + write_bytes) / (1024 * 1024)  # MB
                    stats['disk_activity'] = min(100.0, total_ops * 10)  # Rough estimate

        # Update cache
        _prev_disk_io = current_io
        _prev_disk_time = current_time

    except Exception:
        pass

    return stats


def get_disk_temperature() -> Optional[float]:
    """Get disk temperature from hwmon (nvme, drivetemp)"""
    # Try NVMe drives
    hwmon = find_hwmon_by_name("nvme")
    if hwmon:
        temp = read_file(f"{hwmon}/temp1_input")
        if temp:
            return float(temp) / 1000.0

    # Try drivetemp (SATA drives with S.M.A.R.T.)
    hwmon = find_hwmon_by_name("drivetemp")
    if hwmon:
        temp = read_file(f"{hwmon}/temp1_input")
        if temp:
            return float(temp) / 1000.0

    # Try smartctl fallback
    try:
        result = subprocess.run(
            ['smartctl', '-A', '/dev/sda'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'Temperature' in line or 'Airflow_Temperature' in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part.isdigit() and int(part) < 100:
                            return float(part)
    except:
        pass

    return None


def get_network_stats() -> Dict[str, float]:
    """Get network I/O statistics using psutil"""
    global _prev_net_io, _prev_net_time

    stats = {}

    if not PSUTIL_AVAILABLE:
        return stats

    try:
        import time
        current_time = time.time()
        current_io = psutil.net_io_counters()

        if current_io and _prev_net_io and _prev_net_time:
            time_delta = current_time - _prev_net_time
            if time_delta > 0:
                # Calculate upload/download rates in KB/s
                bytes_sent = current_io.bytes_sent - _prev_net_io.bytes_sent
                bytes_recv = current_io.bytes_recv - _prev_net_io.bytes_recv

                stats['net_up'] = (bytes_sent / time_delta) / 1024  # KB/s
                stats['net_down'] = (bytes_recv / time_delta) / 1024  # KB/s

        # Total transferred in MB
        stats['net_total_up'] = current_io.bytes_sent / (1024 * 1024)  # MB
        stats['net_total_down'] = current_io.bytes_recv / (1024 * 1024)  # MB

        # Update cache
        _prev_net_io = current_io
        _prev_net_time = current_time

    except Exception:
        pass

    return stats


def get_fan_speeds() -> Dict[str, float]:
    """Get fan speeds from hwmon sensors"""
    fans = {}

    # Method 1: Use psutil if available
    if PSUTIL_AVAILABLE:
        try:
            sensors = psutil.sensors_fans()
            if sensors:
                fan_idx = 0
                fan_keys = ['fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2']
                for chip, chip_fans in sensors.items():
                    for fan in chip_fans:
                        if fan_idx < len(fan_keys) and fan.current > 0:
                            fans[fan_keys[fan_idx]] = float(fan.current)
                            fan_idx += 1
                if fans:
                    return fans
        except Exception:
            pass

    # Method 2: Direct hwmon access
    hwmon_base = "/sys/class/hwmon"
    if os.path.exists(hwmon_base):
        fan_idx = 0
        fan_keys = ['fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2']

        for i in range(20):
            hwmon_path = f"{hwmon_base}/hwmon{i}"
            if not os.path.exists(hwmon_path):
                continue

            # Check for fan inputs (fan1_input, fan2_input, etc.)
            for j in range(1, 10):
                fan_file = f"{hwmon_path}/fan{j}_input"
                rpm = read_file(fan_file)
                if rpm and fan_idx < len(fan_keys):
                    try:
                        rpm_val = float(rpm)
                        if rpm_val > 0:
                            fans[fan_keys[fan_idx]] = rpm_val
                            fan_idx += 1
                    except ValueError:
                        pass

    return fans


def get_all_metrics() -> Dict[str, float]:
    """Get all system metrics"""
    metrics = {}
    
    # Add date and time (store as numeric values)
    now = datetime.now()
    metrics['date_year'] = now.year
    metrics['date_month'] = now.month
    metrics['date_day'] = now.day
    metrics['time_hour'] = now.hour
    metrics['time_minute'] = now.minute
    metrics['time_second'] = now.second
    metrics['day_of_week'] = now.weekday()  # 0=Monday, 6=Sunday
    
    # Add special combined keys for display (value doesn't matter, format_metric handles it)
    metrics['date'] = 0  # Placeholder - format_metric generates the actual date string
    metrics['time'] = 0  # Placeholder - format_metric generates the actual time string
    metrics['weekday'] = 0  # Placeholder - format_metric generates weekday string
    
    cpu_temp = get_cpu_temperature()
    if cpu_temp is not None:
        metrics['cpu_temp'] = cpu_temp
    
    cpu_usage = get_cpu_usage()
    if cpu_usage is not None:
        metrics['cpu_percent'] = cpu_usage
    
    cpu_freq = get_cpu_frequency()
    if cpu_freq is not None:
        metrics['cpu_freq'] = cpu_freq
    
    gpu_temp = get_gpu_temperature()
    if gpu_temp is not None:
        metrics['gpu_temp'] = gpu_temp
    
    gpu_usage = get_gpu_usage()
    if gpu_usage is not None:
        metrics['gpu_usage'] = gpu_usage
    
    gpu_clock = get_gpu_clock()
    if gpu_clock is not None:
        metrics['gpu_clock'] = gpu_clock
    
    mem_usage = get_memory_usage()
    if mem_usage is not None:
        metrics['mem_percent'] = mem_usage

    mem_available = get_memory_available()
    if mem_available is not None:
        metrics['mem_available'] = mem_available

    mem_temp = get_memory_temperature()
    if mem_temp is not None:
        metrics['mem_temp'] = mem_temp

    mem_clock = get_memory_clock()
    if mem_clock is not None:
        metrics['mem_clock'] = mem_clock

    # Disk metrics
    disk_temp = get_disk_temperature()
    if disk_temp is not None:
        metrics['disk_temp'] = disk_temp

    disk_stats = get_disk_stats()
    metrics.update(disk_stats)

    # Network metrics
    net_stats = get_network_stats()
    metrics.update(net_stats)

    # Fan metrics
    fan_speeds = get_fan_speeds()
    metrics.update(fan_speeds)

    return metrics


# Format modes matching Windows TRCC (UCXiTongXianShiSub.cs)
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


def format_metric(metric: str, value: float, time_format: int = 0, date_format: int = 0,
                   temp_unit: int = 0) -> str:
    """Format a metric value for display (matches Windows TRCC)

    Args:
        metric: The metric name
        value: The numeric value
        time_format: 0=HH:mm, 1=hh:mm AM/PM, 2=HH:mm (same as 0)
        date_format: 0=yyyy/MM/dd, 1=yyyy/MM/dd, 2=dd/MM/yyyy, 3=MM/dd, 4=dd/MM
        temp_unit: 0=Celsius (°C), 1=Fahrenheit (°F) (Windows myModeSub)
    """
    if metric == 'date':
        now = datetime.now()
        fmt = DATE_FORMATS.get(date_format, DATE_FORMATS[0])
        return now.strftime(fmt)
    elif metric == 'time':
        now = datetime.now()
        fmt = TIME_FORMATS.get(time_format, TIME_FORMATS[0])
        return now.strftime(fmt)
    elif metric == 'weekday':
        now = datetime.now()
        return WEEKDAYS[now.weekday()]
    elif metric == 'day_of_week':
        return WEEKDAYS[int(value)]
    elif metric.startswith('time_') or metric.startswith('date_'):
        return f"{int(value):02d}"
    elif 'temp' in metric:
        # Temperature unit conversion (Windows UCXiTongXianShiSub pattern)
        if temp_unit == 1:  # Fahrenheit
            fahrenheit = value * 9 / 5 + 32
            return f"{fahrenheit:.0f}°F"
        else:  # Celsius (default)
            return f"{value:.0f}°C"
    elif 'percent' in metric or 'usage' in metric or 'activity' in metric:
        return f"{value:.0f}%"
    elif 'freq' in metric or 'clock' in metric:
        if value >= 1000:
            return f"{value/1000:.1f}GHz"
        return f"{value:.0f}MHz"
    elif metric in ('disk_read', 'disk_write'):
        return f"{value:.1f}MB/s"
    elif metric in ('net_up', 'net_down'):
        if value >= 1024:
            return f"{value/1024:.1f}MB/s"
        return f"{value:.0f}KB/s"
    elif metric in ('net_total_up', 'net_total_down'):
        if value >= 1024:
            return f"{value/1024:.1f}GB"
        return f"{value:.0f}MB"
    elif metric.startswith('fan_'):
        return f"{value:.0f}RPM"
    elif metric == 'mem_available':
        if value >= 1024:
            return f"{value/1024:.1f}GB"
        return f"{value:.0f}MB"
    return f"{value:.1f}"


if __name__ == '__main__':
    print("System Info Test")
    print("=" * 40)
    
    metrics = get_all_metrics()
    for key, value in metrics.items():
        print(f"{key}: {format_metric(key, value)}")
