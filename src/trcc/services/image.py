"""Image processing service — RGB565, JPEG, rotation, brightness.

Pure Python (PIL + numpy), no Qt or GUI dependencies.
Absorbed from controllers.py: image_to_rgb565(), apply_rotation(),
_apply_brightness(), byte_order_for().
"""
from __future__ import annotations

import io
import struct
from typing import Any

import numpy as np
from PIL import Image as PILImage

# Cap decompression to 4x the largest LCD (1920x720). Prevents decompression
# bombs from crafted theme images causing OOM.
PILImage.MAX_IMAGE_PIXELS = 1920 * 720 * 4  # 5,529,600 pixels


class ImageService:
    """Stateless image processing utilities."""

    @staticmethod
    def to_rgb565(img: Any, byte_order: str = '>') -> bytes:
        """Convert PIL Image to RGB565 bytes.

        Windows TRCC ImageTo565: big-endian for 320x320 SCSI,
        little-endian otherwise.

        Args:
            img: PIL Image.
            byte_order: '>' for big-endian, '<' for little-endian.
        """
        if img.mode != 'RGB':
            img = img.convert('RGB')

        arr = np.array(img, dtype=np.uint16)
        r = (arr[:, :, 0] >> 3) & 0x1F
        g = (arr[:, :, 1] >> 2) & 0x3F
        b = (arr[:, :, 2] >> 3) & 0x1F
        rgb565 = (r << 11) | (g << 5) | b
        return rgb565.astype(f'{byte_order}u2').tobytes()

    @staticmethod
    def to_jpeg(img: Any, quality: int = 95, max_size: int = 450_000) -> bytes:
        """Compress PIL Image to JPEG bytes.

        Matches C# CompressionImage(): starts at *quality*, reduces by 5
        until output < *max_size*.  USBLCDNew bulk devices expect JPEG
        (cmd=2) instead of raw RGB565.
        """
        if img.mode != 'RGB':
            img = img.convert('RGB')

        for q in range(quality, 4, -5):
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=q)
            data = buf.getvalue()
            if len(data) < max_size:
                return data

        # Fallback: minimum quality
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=5)
        return buf.getvalue()

    @staticmethod
    def apply_rotation(image: Any, rotation: int) -> Any:
        """Apply display rotation to a PIL Image.

        Windows ImageTo565 for square displays:
          directionB 0   → no rotation
          directionB 90  → RotateImg(270°CW) = PIL ROTATE_90 (CCW)
          directionB 180 → RotateImg(180°)   = PIL ROTATE_180
          directionB 270 → RotateImg(90°CW)  = PIL ROTATE_270 (CCW)
        """
        from PIL import Image as PILImage

        if rotation == 90:
            return image.transpose(PILImage.Transpose.ROTATE_270)
        elif rotation == 180:
            return image.transpose(PILImage.Transpose.ROTATE_180)
        elif rotation == 270:
            return image.transpose(PILImage.Transpose.ROTATE_90)
        return image

    @staticmethod
    def apply_brightness(image: Any, percent: int) -> Any:
        """Apply brightness adjustment to image.

        L1=25%, L2=50%, L3=100%. At 100% the image is unchanged.
        """
        if percent >= 100:
            return image
        from PIL import ImageEnhance

        return ImageEnhance.Brightness(image).enhance(percent / 100.0)

    @staticmethod
    def solid_color(r: int, g: int, b: int, w: int, h: int) -> Any:
        """Create a solid-color PIL Image."""
        from PIL import Image as PILImage

        return PILImage.new('RGB', (w, h), (r, g, b))

    @staticmethod
    def resize(img: Any, w: int, h: int) -> Any:
        """Resize PIL Image to target dimensions."""
        from PIL import Image as PILImage

        return img.resize((w, h), PILImage.Resampling.LANCZOS)

    @staticmethod
    def rgb_to_bytes(r: int, g: int, b: int, byte_order: str = '>') -> bytes:
        """Convert single RGB pixel to RGB565 bytes."""
        pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return struct.pack(f'{byte_order}H', pixel)

    @staticmethod
    def byte_order_for(protocol: str, resolution: tuple[int, int]) -> str:
        """Determine RGB565 byte order for a device.

        Big-endian for 320x320 SCSI, little-endian otherwise.
        Matches Windows TRCC ImageTo565 logic.
        """
        if protocol == 'scsi' and resolution != (320, 320):
            return '<'
        return '>'
