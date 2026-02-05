# TRCC Linux

Native Linux app for Thermalright LCD cooler displays. Full port of Windows TRCC 2.0.3.

## Install

```bash
# Dependencies (Fedora)
sudo dnf install sg3_utils python3-tkinter

# Ubuntu/Debian
sudo apt install sg3-utils python3-tk

# Install TRCC
pip install -e .

# Allow device access without sudo
echo 'SUBSYSTEM=="scsi_generic", MODE="0666"' | sudo tee /etc/udev/rules.d/99-trcc.rules
sudo udevadm control --reload-rules
```

## Usage

```bash
trcc gui              # Launch GUI
trcc detect           # Show connected devices
trcc send image.png   # Send image to LCD
trcc test             # Color cycle test
```

## Supported Devices

- FROZEN WARFRAME / FROZEN WARFRAME SE
- FROZEN HORIZON PRO / FROZEN MAGIC PRO
- FROZEN VISION V2 / CORE VISION / ELITE VISION
- LC1 / LC2 / LC3 / LC5 (AIO pump heads)
- AK120 / AX120 / PA120 DIGITAL
- Wonder Vision (CZTV)

Resolutions: 240x240, 320x320, 360x360, 480x480, 480x800

## Features

- 5 starter themes included
- Live system sensors (CPU/GPU temp, usage)
- Video/GIF playback
- Theme editor
- Multi-device support

## Support

If you find this project useful, consider supporting development:

[Buy Me a Coffee](https://buymeacoffee.com/Lexonight1)

## License

GPL-3.0
