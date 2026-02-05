# TRCC Linux Documentation

## Contents

| File | Description |
|------|-------------|
| [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) | Devices, protocol, FBL detection, architecture, CLI commands |
| [09_Handshake_Protocol_Timing.txt](09_Handshake_Protocol_Timing.txt) | Critical handshake timing rules (init once, stream frames) |
| [WINDOWS_UI_HIERARCHY.md](WINDOWS_UI_HIERARCHY.md) | Windows UI coordinates, background image patterns, resource naming |
| [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md) | SCSI security considerations |
| [UI_RESOURCE_MAPPING.md](UI_RESOURCE_MAPPING.md) | Windows resource → Linux asset mapping |

## Quick Links

- [Main README](../README.md) - Installation and usage
- [CLAUDE.md](../CLAUDE.md) - Development guide and architecture overview

## Windows TRCC Reference

The Linux port is based on reverse-engineering the Windows TRCC 2.0.3 application. Key namespaces:

| Namespace | Purpose |
|-----------|---------|
| `TRCC` | Main shell (Form1, UCDevice, UCAbout) |
| `TRCC.CZTV` | LCD controller (FormCZTV = Color Screen Display) |
| `TRCC.DCUserControl` | 50+ reusable UI components |
| `TRCC.LED` / `TRCC.KVMALED6` | LED/RGB controllers |
| `TRCC.Properties` | 670 embedded resources |

See [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) for full details on UI specs and color values.
