"""Create missing posters only; never alter originals, job states or LLM outputs."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packages/shared'))
from video_service.assets import VideoAssetRepository
from video_service.locking import download_lock, job_lock
from video_service.repository import FilesystemJobRepository
from video_service.storage import read_json
from video_service.thumbnails import generate_thumbnail
from video_service.video_probe import inspect_video

parser = argparse.ArgumentParser()
parser.add_argument('--storage-root', required=True)
args = parser.parse_args()
assets = VideoAssetRepository(FilesystemJobRepository(args.storage_root))
failed = False
for manifest in assets.root.glob('*/asset.json'):
    record = read_json(manifest)
    start = time.monotonic()
    with download_lock(assets, record['asset_id']), job_lock(assets, record['asset_id']):
        record = assets.read(record['asset_id'], owner_id=record.get('owner_id'))
        source = assets.source_path(record)
        media = record.get('media') or inspect_video(source)
        poster = generate_thumbnail(source, manifest.parent, media['duration'])
    failed |= poster is None
    print(f"{record['asset_id']} {'ready' if poster else 'failed'} {time.monotonic() - start:.2f}s", flush=True)
raise SystemExit(1 if failed else 0)
