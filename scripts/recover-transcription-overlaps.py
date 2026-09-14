"""Offline recovery of saved responses; dry run unless --apply is supplied."""
import argparse
import json
import os
from pathlib import Path

from media_worker.transcription_recovery import recover_recorded_sentences
from video_service.locking import job_lock
from video_service.models import JobStatus
from video_service.repository import FilesystemJobRepository
from video_service.storage import read_json, write_json_atomic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    repo = FilesystemJobRepository(Path(os.environ['VIDEO_STORAGE_ROOT']))
    with job_lock(repo, args.job_id, 'execution'), job_lock(repo, args.job_id):
        job = repo.read(args.job_id)
        if job.status != JobStatus.FAILED or job.metadata.get('failed_stage') != 'TRANSCRIBING':
            raise ValueError('Recovery requires a failed transcription job')
        work = repo.job_dir(args.job_id) / 'work'
        for path in sorted((work / 'transcription').glob('*.json')):
            saved = read_json(path)
            recovered = recover_recorded_sentences(saved, path.stem, args.job_id, work / 'llm')
            if recovered and args.apply:
                backup = path.with_suffix('.before-overlap-recovery.json.bak')
                if not backup.exists():
                    write_json_atomic(backup, saved)
                saved['results'].update({str(i): result for i, result in recovered.items()})
                saved['failed'] = [i for i in saved.get('failed', []) if i not in recovered]
                saved.setdefault('recoveries', []).append({'kind': 'overlap-validation-v1',
                    'segments': sorted(recovered)})
                saved['progress'].update(completed=len(saved['results']), failed=len(saved['failed']))
                write_json_atomic(path, saved)
                job.metadata['transcription_progress'] = saved['progress']
                repo.save(job)
            print(json.dumps({'queue_id': path.stem, 'recovered_segments': [i + 1 for i in sorted(recovered)],
                              'applied': args.apply and bool(recovered)}))


if __name__ == '__main__':
    main()
