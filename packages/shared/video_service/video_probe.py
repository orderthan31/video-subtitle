"""Read video metadata without extracting audio or invoking any model."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess


def inspect_video(source):
    executable = os.getenv('FFPROBE_PATH') or shutil.which('ffprobe')
    if not executable:
        root = Path(__file__).resolve().parents[3]
        matches = sorted((root / '.tools').glob('ffmpeg-*/bin/ffprobe.exe'))
        executable = str(matches[-1]) if matches else 'ffprobe'
    try:
        result = subprocess.run([executable, '-v', 'error', '-show_entries',
            'format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate:stream_disposition=attached_pic',
            '-of', 'json', str(source)], capture_output=True, timeout=120, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        videos = [s for s in data['streams'] if s['codec_type'] == 'video'
                  and not s.get('disposition', {}).get('attached_pic')]
        if not videos or not math.isfinite(duration) or duration <= 0:
            raise ValueError('A video stream with a valid duration is required')
        video = videos[0]
        width, height = int(video['width']), int(video['height'])
        if width <= 0 or height <= 0:
            raise ValueError('Invalid video dimensions')
        return {'duration': duration, 'width': width, 'height': height,
                'video_codec': video.get('codec_name'), 'frame_rate': video.get('avg_frame_rate'),
                'has_audio': any(s['codec_type'] == 'audio' for s in data['streams'])}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError, TypeError,
            ValueError) as exc:
        raise ValueError('The uploaded file is not a readable video') from exc
