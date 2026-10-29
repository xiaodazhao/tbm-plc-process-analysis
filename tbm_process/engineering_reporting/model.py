"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""
from __future__ import annotations
from datetime import datetime, timezone
from .common import save

def model_stage(stage, request, folder, key, ledger):
    from tbm_process.final_reporting.integrated_llm import call_json_model, ModelResponseError
    save(folder / f'{stage}.request.json', request)
    started = datetime.now(timezone.utc).isoformat()
    try:
        result, response, metadata = call_json_model(request, api_key=key, base_url='https://api.deepseek.com',
                                                    timeout_seconds=240, max_retries=0)
        save(folder / f'{stage}.response.json', response)
        save(folder / f'{stage}.metadata.json', metadata)
        save(folder / f'{stage}.payload.json', result)
        ledger.append({'date': folder.name, 'stage': stage, 'success': True, 'actual_http_retries': 0, **metadata})
        return result
    except Exception as exc:
        if isinstance(exc, ModelResponseError):
            save(folder / f'{stage}.response.json', exc.response)
            save(folder / f'{stage}.metadata.json', exc.metadata)
        # Exception strings can contain service/header details; persist only bounded safe fields.
        failure = {'date': folder.name, 'stage': stage, 'success': False, 'started_utc': started,
                   'error_class': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None),
                   'actual_http_retries': 0}
        save(folder / f'{stage}.failure.json', failure)
        ledger.append(failure)
        return None
