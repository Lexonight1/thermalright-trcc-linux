#!/usr/bin/env python3
"""
GIF and Video Animation Support for TRCC Linux

Handles GIF theme playback and video frame extraction using FFmpeg.
"""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod

from PIL import Image


# Check for FFmpeg availability
def _check_ffmpeg():
    """Check if ffmpeg is available in PATH"""
    try:
        result = subprocess.run(['ffmpeg', '-version'],
                              capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False

FFMPEG_AVAILABLE = _check_ffmpeg()

if not FFMPEG_AVAILABLE:
    print("[!] FFmpeg not available for video support")
    print("    Install: sudo dnf install ffmpeg / sudo apt install ffmpeg")


class AbstractMediaPlayer(ABC):
    """Base class for frame-based media players.

    Provides common playback state and frame navigation logic
    shared by GIF, video, and Theme.zt animation players.
    """

    def __init__(self):
        self.frames: list = []
        self.frame_count: int = 0
        self.current_frame: int = 0
        self.playing: bool = False
        self.loop: bool = True

    def play(self):
        """Start playing."""
        self.playing = True

    def pause(self):
        """Pause playback."""
        self.playing = False

    def stop(self):
        """Stop and reset to beginning."""
        self.playing = False
        self.current_frame = 0

    def reset(self):
        """Reset to first frame."""
        self.current_frame = 0

    def is_playing(self):
        """Check if currently playing."""
        return self.playing

    def get_current_frame(self):
        """Get current frame."""
        if 0 <= self.current_frame < len(self.frames):
            return self.frames[self.current_frame]
        return self.frames[0] if self.frames else None

    def next_frame(self):
        """Advance to next frame and return it."""
        self.current_frame += 1
        if self.current_frame >= self.frame_count:
            if self.loop:
                self.current_frame = 0
            else:
                self.current_frame = self.frame_count - 1
                self.playing = False
        return self.get_current_frame()

    @abstractmethod
    def get_delay(self) -> int:
        """Get delay for current frame in milliseconds."""
        ...

    @abstractmethod
    def close(self):
        """Release resources."""
        ...


class GIFAnimator(AbstractMediaPlayer):
    """Handles GIF animation playback"""

    def __init__(self, gif_path):
        """
        Initialize GIF animator

        Args:
            gif_path: Path to GIF file
        """
        super().__init__()
        self.gif_path = gif_path
        self.image = Image.open(gif_path)

        # Get frame count
        try:
            while True:
                self.image.seek(self.frame_count)
                self.frame_count += 1
        except EOFError:
            pass

        self.delays = []  # ms per frame
        self.speed_multiplier = 1.0  # Speed control (1.0 = normal)

        # Extract all frames and delays
        self._extract_frames()

        # Close original file handle - we have copies of all frames
        if self.image:
            self.image.close()
            self.image = None

    def _extract_frames(self):
        """Extract all frames and their delays"""
        assert self.image is not None
        self.image.seek(0)

        for i in range(self.frame_count):
            self.image.seek(i)

            # Get frame
            frame = self.image.copy().convert('RGB')
            self.frames.append(frame)

            # Get delay (in ms)
            delay = self.image.info.get('duration', 100)  # Default 100ms
            self.delays.append(delay)

    def get_frame(self, frame_index=None):
        """
        Get a specific frame

        Args:
            frame_index: Frame index (None = current frame)

        Returns:
            PIL Image
        """
        if frame_index is None:
            frame_index = self.current_frame

        if 0 <= frame_index < self.frame_count:
            return self.frames[frame_index]
        return self.frames[0]

    def get_delay(self, frame_index=None):
        """
        Get delay for a frame (in ms)

        Args:
            frame_index: Frame index (None = current frame)

        Returns:
            Delay in milliseconds
        """
        if frame_index is None:
            frame_index = self.current_frame

        if 0 <= frame_index < len(self.delays):
            return int(self.delays[frame_index] / self.speed_multiplier)
        return 100

    def set_speed(self, multiplier):
        """
        Set playback speed

        Args:
            multiplier: Speed multiplier (0.5 = half speed, 2.0 = double speed)
        """
        self.speed_multiplier = max(0.1, min(10.0, multiplier))

    def is_last_frame(self):
        """Check if on last frame"""
        return self.current_frame == self.frame_count - 1

    def close(self):
        """Close the GIF file handle"""
        if self.image:
            self.image.close()
            self.image = None

    def __del__(self):
        """Cleanup on deletion"""
        self.close()


class GIFThemeLoader:
    """Loads GIF themes and converts to TRCC format"""

    @staticmethod
    def load_gif_theme(gif_path, target_size=(480, 128)):
        """
        Load GIF theme and prepare for LCD display

        Args:
            gif_path: Path to GIF file
            target_size: Target display size

        Returns:
            GIFAnimator instance
        """
        return GIFAnimator(gif_path)

    @staticmethod
    def gif_to_frames(gif_path, output_dir, target_size=(480, 128)):
        """
        Extract GIF frames to individual PNG files

        Args:
            gif_path: Path to GIF file
            output_dir: Output directory for frames
            target_size: Target size for frames

        Returns:
            Number of frames extracted
        """
        import os

        animator = GIFAnimator(gif_path)
        os.makedirs(output_dir, exist_ok=True)

        for i in range(animator.frame_count):
            frame = animator.get_frame(i)

            # Resize if needed
            if frame.size != target_size:
                frame = frame.resize(target_size, Image.Resampling.LANCZOS)

            # Save frame
            frame_path = os.path.join(output_dir, f"frame_{i:04d}.png")
            frame.save(frame_path)

            # Save delay info
            delay = animator.get_delay(i)
            with open(os.path.join(output_dir, f"frame_{i:04d}.txt"), 'w') as f:
                f.write(str(delay))

        # Save first frame as 00.png (background)
        frame = animator.get_frame(0)
        if frame.size != target_size:
            frame = frame.resize(target_size, Image.Resampling.LANCZOS)
        frame.save(os.path.join(output_dir, "00.png"))

        print(f"[+] Extracted {animator.frame_count} frames to {output_dir}")
        return animator.frame_count


class VideoPlayer(AbstractMediaPlayer):
    """Video player using FFmpeg pipe for frame decoding.

    Decodes all frames via FFmpeg pipe to memory (no temp BMP files).
    FFmpeg exits after loading — zero CPU during playback.
    """

    def __init__(self, video_path, target_size=(320, 320)):
        if not FFMPEG_AVAILABLE:
            raise RuntimeError("FFmpeg not available. Install it:\n"
                             "  sudo dnf install ffmpeg / sudo apt install ffmpeg")

        super().__init__()
        self.video_path = video_path
        self.target_size = target_size
        self.fps = 16  # Windows: originalImageHz = 16

        self._load_via_pipe()

    def _load_via_pipe(self):
        """Decode all frames through FFmpeg pipe — no temp files."""
        w, h = self.target_size
        result = subprocess.run([
            'ffmpeg',
            '-i', self.video_path,
            '-r', str(self.fps),
            '-vf', f'scale={w}:{h}',
            '-f', 'rawvideo',
            '-pix_fmt', 'rgb24',
            '-loglevel', 'error',
            'pipe:1'
        ], capture_output=True, timeout=300)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr.decode()[:200]}")

        raw = result.stdout
        frame_size = w * h * 3
        self.frames = []
        for i in range(0, len(raw), frame_size):
            chunk = raw[i:i + frame_size]
            if len(chunk) < frame_size:
                break
            self.frames.append(Image.frombytes('RGB', (w, h), chunk))

        self.frame_count = len(self.frames)

    def get_current_frame(self):
        """Get current frame as PIL Image."""
        if 0 <= self.current_frame < len(self.frames):
            return self.frames[self.current_frame]
        return self.frames[0] if self.frames else None

    def get_delay(self):
        """Get delay between frames in milliseconds."""
        return int(1000 / self.fps)

    def close(self):
        """Release resources."""
        self.frames = []

    def __del__(self):
        self.close()

    @staticmethod
    def extract_frames(video_path, output_dir, target_size=(320, 320), max_frames=None):
        """
        Extract video frames to PNG files

        Args:
            video_path: Path to video file
            output_dir: Directory to save frames
            target_size: Frame size
            max_frames: Max frames to extract (None = all)

        Returns:
            Number of frames extracted
        """
        os.makedirs(output_dir, exist_ok=True)

        if FFMPEG_AVAILABLE:
            return VideoPlayer._extract_frames_ffmpeg(video_path, output_dir, target_size, max_frames)
        else:
            print("[!] FFmpeg not available for video extraction")
            return 0

    @staticmethod
    def _extract_frames_ffmpeg(video_path, output_dir, target_size, max_frames):
        """
        Extract frames using FFmpeg (matching Windows TRCC behavior).
        Command: ffmpeg -i "{VIDEO}" -y -s {W}x{H} -f image2 "{OUTPUT}%04d.png"
        """
        w, h = target_size

        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-y',  # Overwrite
            '-vf', f'scale={w}:{h}',
        ]

        # Add frame limit if specified
        if max_frames:
            cmd.extend(['-vframes', str(max_frames)])

        cmd.extend([
            '-f', 'image2',
            os.path.join(output_dir, 'frame_%04d.png')
        ])

        print("[*] Extracting frames with FFmpeg...")
        print(f"    Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=600)
            if result.returncode != 0:
                print(f"[!] FFmpeg error: {result.stderr.decode()[:200]}")
                return 0
        except subprocess.TimeoutExpired:
            print("[!] FFmpeg timed out")
            return 0
        except Exception as e:
            print(f"[!] FFmpeg failed: {e}")
            return 0

        # Count extracted frames
        extracted = len([f for f in os.listdir(output_dir) if f.startswith('frame_') and f.endswith('.png')])
        print(f"[+] Extracted {extracted} frames")
        return extracted


class ThemeZtPlayer(AbstractMediaPlayer):
    """
    Plays Theme.zt animation files.

    Theme.zt format (Windows UCVideoCut.BmpToThemeFile):
    - byte: 0xDC magic (220)
    - int32: frame_count
    - int32[frame_count]: timestamps in ms
    - for each frame: int32 size + JPEG bytes

    This player loads the entire animation into memory for smooth playback.
    """

    def __init__(self, zt_path, target_size=None):
        """
        Load Theme.zt animation.

        Args:
            zt_path: Path to Theme.zt file
            target_size: Optional (width, height) to resize frames
        """
        import io
        import struct

        super().__init__()
        self.zt_path = zt_path
        self.target_size = target_size
        self.timestamps = []

        # Parse Theme.zt file
        with open(zt_path, 'rb') as f:
            # Read magic byte
            magic = struct.unpack('B', f.read(1))[0]
            if magic != 0xDC:
                raise ValueError(f"Invalid Theme.zt magic: 0x{magic:02X}, expected 0xDC")

            # Read frame count
            frame_count = struct.unpack('<i', f.read(4))[0]

            # Read timestamps
            for _ in range(frame_count):
                ts = struct.unpack('<i', f.read(4))[0]
                self.timestamps.append(ts)

            # Read frame data (JPEG bytes)
            for i in range(frame_count):
                size = struct.unpack('<i', f.read(4))[0]
                jpeg_data = f.read(size)

                # Decode JPEG to PIL Image
                img = Image.open(io.BytesIO(jpeg_data))

                # Resize if needed
                if target_size and img.size != target_size:
                    img = img.resize(target_size, Image.Resampling.LANCZOS)

                # Convert to RGB
                if img.mode != 'RGB':
                    img = img.convert('RGB')

                self.frames.append(img)

        self.frame_count = len(self.frames)

        # Calculate delays from timestamps
        self.delays = []
        for i in range(len(self.timestamps)):
            if i < len(self.timestamps) - 1:
                delay = self.timestamps[i + 1] - self.timestamps[i]
            else:
                # Last frame - use same delay as previous
                delay = self.delays[-1] if self.delays else 42  # ~24fps default
            self.delays.append(max(1, delay))

    def get_current_frame(self):
        """Get current frame as PIL Image (copy to prevent mutation)."""
        if 0 <= self.current_frame < len(self.frames):
            return self.frames[self.current_frame].copy()
        return None

    def get_delay(self):
        """Get delay for current frame in ms."""
        if self.current_frame < len(self.delays):
            return self.delays[self.current_frame]
        return 42  # ~24fps default

    def seek(self, position):
        """Seek to position (0.0-1.0)."""
        position = max(0.0, min(1.0, position))
        self.current_frame = int(position * (len(self.frames) - 1))

    def get_progress(self):
        """Get current playback progress (0-100)."""
        if len(self.frames) <= 1:
            return 0
        return int((self.current_frame / (len(self.frames) - 1)) * 100)

    def close(self):
        """Release resources."""
        for frame in self.frames:
            if hasattr(frame, 'close'):
                frame.close()
        self.frames = []


def test_video_player():
    """Test video player"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python gif_animator.py <video_or_gif_file>")
        print("       python gif_animator.py --extract <video> <output_dir>")
        print("       python gif_animator.py <theme.zt>")
        return

    if sys.argv[1] == '--extract' and len(sys.argv) >= 4:
        # Extract mode
        video_path = sys.argv[2]
        output_dir = sys.argv[3]
        max_frames = int(sys.argv[4]) if len(sys.argv) > 4 else None

        if video_path.lower().endswith('.gif'):
            GIFThemeLoader.gif_to_frames(video_path, output_dir)
        else:
            VideoPlayer.extract_frames(video_path, output_dir, max_frames=max_frames)
        return

    file_path = sys.argv[1]

    if file_path.lower().endswith('.gif'):
        print(f"[*] Loading GIF: {file_path}")
        animator = GIFAnimator(file_path)
        print(f"[+] Frames: {animator.frame_count}")
        print(f"[+] Delays: {animator.delays[:10]}...")
    elif file_path.lower().endswith('.zt'):
        print(f"[*] Loading Theme.zt: {file_path}")
        player = ThemeZtPlayer(file_path)
        print(f"[+] Frames: {player.frame_count}")
        print(f"[+] Timestamps: {player.timestamps[:10]}...")
        print(f"[+] Delays: {player.delays[:10]}...")
        # Show first frame info
        if player.frames:
            first = player.frames[0]
            print(f"[+] Frame size: {first.size}")
        player.close()
    else:
        print(f"[*] Loading Video: {file_path}")
        if not FFMPEG_AVAILABLE:
            print("[!] FFmpeg not installed. Run: sudo dnf install ffmpeg")
            return
        player = VideoPlayer(file_path)
        print(f"[+] Frames: {player.frame_count}")
        print(f"[+] FPS: {player.fps}")
        print(f"[+] Delay per frame: {player.get_delay()}ms")
        player.close()

    print("\n[✓] Test complete!")


if __name__ == '__main__':
    test_video_player()
