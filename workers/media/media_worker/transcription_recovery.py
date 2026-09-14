"""Revalidate recorded queue responses without contacting a provider."""
import json
import re

from .sentence_transcription import validate_sentences


def recover_recorded_sentences(saved, queue_id, job_id, trace_root):
    recovered = {}
    for entry in reversed(saved.get('history', [])):
        index = entry.get('segment')
        if type(index) is not int or not 0 <= index < saved['count']:
            continue
        if str(index) in saved['results'] or index in recovered:
            continue
        diagnostics = entry.get('diagnostics', {})
        for event in diagnostics.get('events', []):
            trace_id = event.get('trace_id', '')
            if not re.fullmatch(r'\d{8}T\d{12}Z-[a-f0-9]{32}', trace_id):
                continue
            try:
                folder = trace_root / trace_id
                request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
                identity = request['diagnostic_context']
                if (identity['job_id'] != job_id or identity['queue_id'] != queue_id
                        or identity['segment'] != index or identity['stage'] != 'transcription'):
                    continue
                response = json.loads((folder / 'response.json').read_text(encoding='utf-8'))
                if response['status'] != 200:
                    continue
                body = json.loads(response['body'])
                candidate = body['candidates'][0]
                if candidate.get('finishReason') != 'STOP':
                    continue
                items = json.loads(''.join(p.get('text', '') for p in candidate['content']['parts']))
                window = request['window']
                if window['clock'] != 'preprocessed_audio' or window['split_depth'] != 0:
                    continue
                recovered[index] = validate_sentences(items, window['end_seconds'] - window['start_seconds'])
                break
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                continue
    return recovered
