"""Expose only published subtitle artifacts, never provider traces or source paths."""
import math

from video_service.models import JobStatus
from video_service.storage import read_json, resolve_under


def read_preview(repository, record):
    def path(*parts):
        return resolve_under(repository.storage_root, record.job_id, *parts)

    expired = bool(record.metadata.get('results_expired_at'))
    tracks = {}
    draft = None
    draft_path = path('work', 'subtitle-draft.json')
    if record.metadata.get('subtitle_revision_dir'):
        draft_path = path('output', record.metadata['subtitle_revision_dir'], 'draft.json')
    if not expired and draft_path.is_file():
        draft = read_json(draft_path).get('tracks', {})
    for name, filename in [('original', 'transcript'), ('translated', 'translated')]:
        track = {'available': False, 'partial': False, 'cues': []}
        if not expired:
            full = path('work', filename + '.json')
            partial = path('work', 'partial-' + filename + '.json')
            cues = None
            if draft is not None and name in draft:
                cues = draft[name]
            elif full.is_file():
                cues = read_json(full)
            elif partial.is_file():
                cues = read_json(partial)['segments']
                track['partial'] = True
            if cues is not None:
                normalized = []
                for cue in cues:
                    start, end = float(cue['start']), float(cue['end'])
                    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                        raise ValueError('Invalid preview timestamps')
                    normalized.append({'start': start, 'end': end, 'text': str(cue['text'])})
                track.update(available=True, cues=normalized)
        tracks[name] = track
    return {'tracks': tracks, 'expired': expired,
        'video_available': record.status == JobStatus.COMPLETED and not expired and path('output', 'final.mp4').is_file()}
