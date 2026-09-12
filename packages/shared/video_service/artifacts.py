"""Immutable, asset-scoped subtitle inputs and workflow-local snapshots."""
from __future__ import annotations

import hashlib
import json
import math
import re
from uuid import uuid4

import srt

from .assets import AssetNotFoundError
from .models import utc_now_iso
from .storage import read_json, resolve_under, write_json_atomic


def validate_cues(cues, duration=None):
    if not isinstance(cues, list) or not 1 <= len(cues) <= 10000:
        raise ValueError('Subtitles must contain 1 to 10000 cues')
    previous = -1
    normalized = []
    for cue in cues:
        start, end = float(cue['start']), float(cue['end'])
        text = cue['text']
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or start < previous:
            raise ValueError('Invalid subtitle timestamps')
        if duration is not None and end > duration + 0.1:
            raise ValueError('Subtitle exceeds video duration')
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError('Invalid subtitle text')
        normalized.append({'start': start, 'end': end, 'text': text})
        previous = start
    return normalized


def cue_digest(cues):
    return hashlib.sha256(json.dumps(cues, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


class SubtitleArtifactRepository:
    def __init__(self, assets):
        self.assets = assets

    def _path(self, asset_id, artifact_id):
        if not re.fullmatch(r'[0-9a-f]{32}', artifact_id):
            raise AssetNotFoundError(artifact_id)
        return resolve_under(self.assets.directory(asset_id), 'subtitles', artifact_id + '.json')

    def read(self, asset_id, artifact_id, *, owner_id=None):
        self.assets.read(asset_id, owner_id=owner_id)
        path = self._path(asset_id, artifact_id)
        if not path.is_file():
            raise AssetNotFoundError(artifact_id)
        record = read_json(path)
        if record['asset_id'] != asset_id or record['artifact_id'] != artifact_id:
            raise ValueError('Invalid subtitle reference')
        if cue_digest(record['cues']) != record['sha256']:
            raise ValueError('Subtitle artifact integrity check failed')
        return record

    def list(self, asset_id, *, owner_id=None):
        self.assets.read(asset_id, owner_id=owner_id)
        paths = (self.assets.directory(asset_id) / 'subtitles').glob('*.json')
        return sorted(({k: v for k, v in self.read(asset_id, p.stem, owner_id=owner_id).items()
                        if k not in {'cues', 'original_srt'}} for p in paths),
                      key=lambda r: (r['created_at'], r['artifact_id']), reverse=True)

    def publish(self, asset_id, *, owner_id=None, filename, language, cues, provenance,
                before_write, original_srt=None):
        with self.assets.lock():
            asset = self.assets.read(asset_id, owner_id=owner_id)
            if not re.fullmatch(r'(?:auto|[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)', language):
                raise ValueError('Invalid subtitle language')
            duration = asset.get('media', {}).get('duration')
            cues = validate_cues(cues, duration)
            artifact_id = uuid4().hex
            record = {'schema_version': 1, 'asset_id': asset_id, 'artifact_id': artifact_id,
                      'kind': 'subtitle', 'filename': filename, 'language': language,
                      'created_at': utc_now_iso(), 'provenance': provenance,
                      'sha256': cue_digest(cues), 'cues': cues, 'cue_count': len(cues)}
            if original_srt is not None:
                record['original_srt'] = original_srt
            write_json_atomic(self._path(asset_id, artifact_id), record, before_write=before_write)
            return record

    def attach_srt(self, asset_id, *, owner_id=None, filename, language, content, before_write):
        self.assets.read(asset_id, owner_id=owner_id)
        if not filename.lower().endswith('.srt') or not 1 <= len(filename) <= 255:
            raise ValueError('An SRT filename is required')
        if len(content.encode('utf-8')) > 4 * 1024 * 1024:
            raise ValueError('SRT exceeds 4 MiB')
        try:
            cues = [{'start': item.start.total_seconds(), 'end': item.end.total_seconds(),
                     'text': item.content} for item in srt.parse(content.lstrip('\ufeff'), ignore_errors=False)]
        except srt.SRTParseError as exc:
            raise ValueError('Invalid SRT content') from exc
        return self.publish(asset_id, owner_id=owner_id, filename=filename, language=language,
            cues=cues, provenance={'kind': 'attachment'}, before_write=before_write, original_srt=content)

    def snapshot(self, asset_id, artifact_id, destination, *, owner_id=None, before_write):
        """Caller holds the asset registry lock until its referencing job is published."""
        record = self.read(asset_id, artifact_id, owner_id=owner_id)
        write_json_atomic(destination, record, before_write=before_write)
        return {'asset_id': asset_id, 'artifact_id': artifact_id, 'sha256': record['sha256'],
                'language': record['language'], 'cue_count': record['cue_count']}
