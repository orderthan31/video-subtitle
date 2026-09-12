"""Publish a complete subtitle revision with a single job metadata commit."""
import json
from uuid import uuid4

from video_service.review import track_languages, validate_tracks
from video_service.storage import read_json, resolve_under
from video_service.subtitles import segment_subtitles, segments_to_srt
from video_service.sami import segments_to_sami
from video_service.transcript import TranscriptSegment


def final_track_languages(record):
    languages = track_languages(record.options)
    if record.metadata.get('workflow'):
        files = record.metadata.get('result_files', [])
        languages = {name: language for name, language in languages.items() if name + '.srt' in files}
    if not languages:
        raise FileNotFoundError('No published subtitles')
    return languages


def read_final_draft(repository, record):
    revision_dir = record.metadata.get('subtitle_revision_dir')
    if revision_dir:
        return read_json(resolve_under(repository.storage_root, record.job_id, 'output', revision_dir, 'draft.json'))
    work = repository.job_dir(record.job_id) / 'work'
    languages = final_track_languages(record)
    if (work / 'subtitle-draft.json').is_file():
        draft = read_json(work / 'subtitle-draft.json')
        tracks, duration = draft['tracks'], float(draft['duration'])
    else:
        duration = float(record.metadata.get('duration') or record.metadata.get('source_duration') or 0)
        tracks = {}
        for name, language in languages.items():
            source = work / ('transcript.json' if name == 'original' else name + '.json')
            segments = [TranscriptSegment.from_dict(cue) for cue in read_json(source)]
            preserve_input = record.metadata.get('subtitle_input') and (
                name == 'original' or record.metadata.get('workflow', {}).get('template') == 'encode')
            tracks[name] = [cue.to_dict() for cue in (segments if preserve_input else segment_subtitles(segments,
                line_width=24 if language.split('-')[0] in {'auto','ko','ja','zh'} else 42))]
    imported_layout = bool(record.metadata.get('subtitle_input'))
    validate_tracks(tracks, record.options, duration, allow_empty=True, languages=languages,
                    allow_imported_layout=imported_layout)
    return {'revision': 0, 'duration': duration, 'languages': languages, 'tracks': tracks,
            'allow_imported_layout': imported_layout}


def save_final_draft(repository, record, tracks, duration, revision, capacity_check):
    languages = final_track_languages(record)
    imported_layout = bool(record.metadata.get('subtitle_input'))
    validate_tracks(tracks, record.options, duration, allow_empty=True, languages=languages,
                    allow_imported_layout=imported_layout)
    draft = {'revision': revision, 'duration': duration, 'languages': languages, 'tracks': tracks,
             'allow_imported_layout': imported_layout}
    payloads = {'draft.json': json.dumps(draft, ensure_ascii=False, indent=2) + '\n'}
    for name, language in languages.items():
        cues = [TranscriptSegment.from_dict(cue) for cue in tracks[name]]
        payloads[name + '.srt'] = segments_to_srt(cues)
        if name != 'original':
            payloads[name + '.smi'] = segments_to_sami(segment_subtitles(cues) if imported_layout else cues, language)
    capacity_check(sum(len(text.encode('utf-8')) for text in payloads.values()) + 4096)
    relative = f'subtitle-edits/{revision}-{uuid4().hex}'
    directory = resolve_under(repository.storage_root, record.job_id, 'output', relative)
    directory.mkdir(parents=True)
    for filename, text in payloads.items():
        (directory / filename).write_text(text, encoding='utf-8', newline='\n')
    # Readers see either the previous complete revision or this complete revision.
    record.metadata.update(subtitle_revision_dir=relative, subtitle_revision=revision, video_subtitles_outdated=True)
    repository.save(record)
    return draft
