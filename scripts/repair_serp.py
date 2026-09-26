"""Import a job-scoped browser SERP and rebuild its dependent outputs.

Default: dry run. --apply backs up the original job/artifacts before any writes.
Run from the repository root: python -m scripts.repair_serp snapshot.json --apply
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from dotenv import load_dotenv
import requests

from pipeline import db
from pipeline.serp_sources import verified_serp
from pipeline.step_plan import build_step_plan, requires_rate_limit_delay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path, default=Path('repair-backups'))
    args = parser.parse_args()
    load_dotenv('.env')
    snapshot = json.loads(args.snapshot.read_text())
    job = db.get_job(snapshot['job_id'])
    if job['status'] in ('running', 'queued'):
        raise RuntimeError('実行中の記事は上書きできません。')
    _, source = verified_serp(snapshot, job['main_keyword'], job['id'])
    steps = build_step_plan(job)
    print(json.dumps({'job_id': job['id'], 'query': job['main_keyword'],
                      'source': source, 'steps': [k for k, _ in steps], 'apply': args.apply}, ensure_ascii=False))
    if not args.apply:
        return
    response = requests.get(f'{db._base()}/artifacts', params={'job_id': f"eq.{job['id']}"},
                            headers=db._headers(), timeout=20)
    response.raise_for_status()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = args.backup_dir / f"{job['id']}-{stamp}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps({'job': job, 'artifacts': response.json()}, ensure_ascii=False, indent=2))
    backup.chmod(0o600)
    print(f'Backup: {backup.resolve()}', flush=True)
    db.upsert_artifact(job_id=job['id'], step='serp_verified', content_type='application/json',
                       content_text=json.dumps(snapshot, ensure_ascii=False), meta={'source': source})
    # Derived automatic target lengths must be recalculated from corrected competitors.
    if '（上限' in (job.get('word_count_setting') or ''):
        db.update_job_word_count_setting(job['id'], '')
    db.update_job_error(job['id'], '')
    db.update_job_status(job['id'], 'running')
    try:
        for index, (key, fn) in enumerate(steps):
            db.update_job_step(job['id'], key)
            print(f'Running: {key}', flush=True)
            fn(job['id'], job['main_keyword'])
            if index < len(steps)-1 and requires_rate_limit_delay(key):
                time.sleep(15)
        db.update_job_step(job['id'], None)
        db.update_job_status(job['id'], 'done')
    except Exception as exc:
        db.update_job_step(job['id'], None)
        db.update_job_status(job['id'], 'failed')
        db.update_job_error(job['id'], str(exc))
        raise
    print('Repair completed.', flush=True)


if __name__ == '__main__':
    main()
