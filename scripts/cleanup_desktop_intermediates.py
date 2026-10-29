"""Explicit, recoverable cleanup after the three-date integration acceptance.

No wildcard deletion, no raw-data cleanup, no emptying the Trash.
"""
from datetime import datetime
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT.parent
OLD_INTERMEDIATE_DIRECTORIES = [
    'TBM_PLC响应与多源关系实验_20261027',
    'TBM_新版日报V2_真实验证_20261027',
    '色季拉山_PLC地质接入核查_20261027',
    'TBM_地质描述单元盘点_20261027',
    'TBM_新版三章日报内容纠偏_20261027',
    'TBM_多日期验证与跨区段探索_20261028',
    '风险资料可视化_20261025',
    'TBM_多源证据图联合日报_v1_20261027',
    'TBM_PLCResponse_只读核查_20261027',
    'TBM_日报展示与素描更新_20261028',
    'TBM_新版三章日报开发验证_20261027',
    'TBM_地质机器响应分析层修正_20261028',
    'TBM_20240426图谱关系审计_20261027',
    'TBM_日报统一标准_20261028',
    'TBM_日报LLM润色排版对照_20261028',
    'TBM_TSP_HSP属性关系实验_20261027',
    'TBM_新版日报V2_DeepSeek三天_20261027',
    'TBM_三章日报工程分析修正_20261028',
    'TBM_项目清理备份_20261027_161801',
]
FINAL_DIRECTORIES = [
    '13',
    'TBM_论文研究候选冻结版_20261028',
    'TBM_可靠性修复与冻结验收_20261028',
    'TBM9_资料盘点_20261025',
]


def size(path):
    if path.is_file():
        return path.stat().st_size
    return sum(p.lstat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())


def main():
    report_root = ROOT/'outputs/unified_engineering_release_20261029'
    for day in ['2024-01-24', '2024-02-28', '2024-04-26']:
        folder = report_root/day
        result = json.loads((folder/'artifact.validation.json').read_text())
        if not result['passed'] or not result['PDF_text_same_as_checked_model_text']:
            raise ValueError('cleanup_blocked_by_failed_acceptance:'+day)
        if not (folder/'editor.request.json').is_file():
            raise ValueError('accepted_editor_stage_missing:'+day)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    audit_dir = ROOT/'outputs/desktop_cleanup'/stamp
    audit_dir.mkdir(parents=True)
    trash = Path.home()/'.Trash'/('TBM_intermediates_'+stamp)
    archive = ROOT/'archives'/('desktop_history_'+stamp)
    trash.mkdir(parents=True, exist_ok=False)
    archive.mkdir(parents=True, exist_ok=False)
    plan = []
    for name in OLD_INTERMEDIATE_DIRECTORIES:
        path = DESKTOP/name
        if path.exists():
            plan.append({'source': str(path), 'destination': str(trash/name), 'action': 'move_to_trash',
                         'reason': 'superseded_intermediate_or_old_cleanup_backup', 'bytes': size(path)})
    for name in FINAL_DIRECTORIES:
        path = DESKTOP/name
        if path.exists():
            plan.append({'source': str(path), 'destination': str(archive/name), 'action': 'preserve_archive',
                         'reason': 'historical_final_research_or_pilot_sources_and_model_not_deleted', 'bytes': size(path)})
    for path in DESKTOP.iterdir():
        if path.is_file() and path.name.startswith('TBM_') and '.zip' in path.name:
            plan.append({'source': str(path), 'destination': str(archive/path.name), 'action': 'preserve_archive',
                         'reason': 'final_delivery_and_integrity_sidecar', 'bytes': size(path)})
    (audit_dir/'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    completed = []
    for row in plan:
        source, target = Path(row['source']), Path(row['destination'])
        if source.parent != DESKTOP or source.is_symlink() or target.exists():
            raise ValueError('unsafe_cleanup_path')
        shutil.move(str(source), str(target))
        if source.exists() or not target.exists():
            raise RuntimeError('cleanup_move_not_verified')
        completed.append(row)
        (audit_dir/'completed.json').write_text(json.dumps(completed, ensure_ascii=False, indent=2))
    result = {'moved_to_trash': sum(r['action']=='move_to_trash' for r in completed),
        'archived': sum(r['action']=='preserve_archive' for r in completed),
        'bytes_to_trash': sum(r['bytes'] for r in completed if r['action']=='move_to_trash'),
        'archive': str(archive), 'trash': str(trash), 'Trash_emptied': False,
        'protected': ['伯舒拉岭数据', 'TBM_时空地质证据库_v1_20261027',
                      'TBM9_地质证据与PLC对照_v1_20261025', 'TBM9_PLC_修复标准化_20261025',
                      'tbm-plc-process-analysis', 'other_projects_and_personal_documents']}
    (audit_dir/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
