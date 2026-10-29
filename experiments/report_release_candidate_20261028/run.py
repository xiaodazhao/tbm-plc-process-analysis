from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from tbm_process.reporting_v2.facts import digest,write_json
from experiments.report_reliability_freeze_20261028.run import ROOT,OLD,MODIFIED,PROTECTED,DB,read,rebuild

OUT=Path.home()/'Desktop/TBM_论文研究候选冻结版_20261028/run1'
PREVIOUS=Path.home()/'Desktop/TBM_可靠性修复与冻结验收_20261028/run1'
FILES=MODIFIED+['tbm_process/evidence_graph/report_reliability.py']


CATALOG=OLD/'A_reports/2024-10-23/time_admissible_catalog.json'


def protected():
    return {str(p):digest(p) if p.exists() else 'MISSING_AT_START'
            for p in [DB,CATALOG,*[ROOT/f for f in PROTECTED]]}


def initialize():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'PROTOCOL.json').exists():raise FileExistsError('already_initialized')
    for f in FILES:
        dest=OUT/'before_source'/f;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            if digest(dest)!=digest(ROOT/f):raise ValueError('partial_initialization_source_changed:'+f)
        else:shutil.copy2(ROOT/f,dest)
    write_json(OUT/'protected.before.json',protected())
    write_json(OUT/'previous_results.before.json',{str(p):digest(p) for root in [OLD,PREVIOUS] for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    write_json(OUT/'PROTOCOL.json',{
        'scope':'repair_known_contract_errors_and_activate_existing_LLM_planner',
        'no_PLC_algorithm_or_database_change':True,'human_acceptance':'pending_not_simulated',
        'new_user_API_authorization':'2026-10-28_explicit_no_call_limit',
        'local_safety_limit':12,'quality_retries_per_date':1,'transport_retries':0,
        'independent_days':2,'one_day_must_have_usable_segment_comparison':True,
        'no_prompt_tuning_or_date_replacement_after_independent_generation':True,
        'statistical_efficacy_not_established_by_this_small_acceptance':True})
    write_json(OUT/'DATA_ACCESS.json',{'original_database':str(DB),'exists':DB.exists(),
        'fallback':'unchanged_archived_time_admissible_catalog_for_dates_not_later_than_2024-10-23',
        'catalog':str(CATALOG),'sha256':digest(CATALOG),
        'limitation':'SQLite query layer cannot be revalidated while original database is unavailable; no database reconstructed'})
    print(str(OUT),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['initialize','replay','select','generate','repair-interface','verify-patch','deliver']);a=parser.parse_args()
    if a.stage=='initialize':initialize();return
    from .workflow import replay,select,generate,repair_interface,verify_patch,deliver
    {'replay':replay,'select':select,'generate':generate,'repair-interface':repair_interface,'verify-patch':verify_patch,'deliver':deliver}[a.stage]()


if __name__=='__main__':main()
