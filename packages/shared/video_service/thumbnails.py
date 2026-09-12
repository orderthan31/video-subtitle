"""Bounded, model-free poster generation. Never decode video on an image GET."""
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

FILENAME = 'thumbnail-v1.jpg'
WIDTH, HEIGHT = 480, 270
logger = logging.getLogger(__name__)


def candidate_times(duration):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid video duration')
    return [duration * fraction for fraction in (0.10, 0.35, 0.60)]


def frame_score(frame):
    # Sparse luminance samples penalize near-black/white frames, not specific subjects.
    luminance = [(frame[i] * 299 + frame[i + 1] * 587 + frame[i + 2] * 114) / 1000
                 for i in range(0, len(frame) - 2, 24)]
    if not luminance:
        return float('-inf')
    mean = sum(luminance) / len(luminance)
    variance = sum((x - mean) ** 2 for x in luminance) / len(luminance)
    clipped = sum(x < 18 or x > 242 for x in luminance) / len(luminance)
    return math.sqrt(variance) - clipped * 100


def generate_thumbnail(source, directory, duration):
    """Best effort; a poster failure must not turn a complete upload into a failure."""
    destination = Path(directory) / FILENAME
    if destination.is_file():
        return destination
    executable = os.getenv('FFMPEG_PATH') or shutil.which('ffmpeg')
    if not executable:
        matches = sorted((Path(__file__).resolve().parents[3] / '.tools').glob('ffmpeg-*/bin/ffmpeg.exe'))
        executable = str(matches[-1]) if matches else 'ffmpeg'
    temporary = destination.with_name('thumbnail-' + uuid4().hex + '.jpg')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        frames = []
        for seconds in candidate_times(float(duration)):
            try:
                result = subprocess.run([executable, '-nostdin', '-v', 'error', '-threads', '2',
                    '-ss', f'{seconds:.6f}', '-i', str(source), '-map', '0:V:0', '-an', '-sn',
                    '-vf', f'scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1',
                    '-frames:v', '1', '-threads', '2', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                    capture_output=True, timeout=12, check=True, creationflags=flags)
                if len(result.stdout) == WIDTH * HEIGHT * 3:
                    frames.append(result.stdout)
            except (subprocess.SubprocessError, OSError):
                continue
        if not frames:
            logger.warning('No thumbnail candidate could be decoded for %s', Path(source).name)
            return None
        best = max(frames, key=frame_score)
        subprocess.run([executable, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo',
            '-pixel_format', 'rgb24', '-video_size', f'{WIDTH}x{HEIGHT}', '-i', 'pipe:0',
            '-frames:v', '1', '-threads', '2', '-q:v', '4', '-update', '1', str(temporary)],
            input=best, capture_output=True, timeout=5, check=True, creationflags=flags)
        if temporary.stat().st_size == 0:
            return None
        temporary.replace(destination)
        return destination
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        logger.warning('Thumbnail generation unavailable for %s', Path(source).name)
        return None
    finally:
        temporary.unlink(missing_ok=True)
