"""One-time mechanical migration of reviewed pilot functions and local resources.

No source data or frozen algorithms are rewritten. Run explicitly, not at report time.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT.parent / '13'
DEST = ROOT / 'tbm_process/engineering_reporting'
RES = ROOT / 'local_data/engineering_context'
ORIGINS = []


def extract(source, names):
    path = PILOT / source
    text = path.read_text()
    tree = ast.parse(text)
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    if set(names) != {n.name for n in selected}:
        raise ValueError((source, names))
    ORIGINS.append({'source': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'definitions': names})
    return '\n\n'.join(ast.get_source_segment(text, n) for n in selected) + '\n'


def module(name, header, body):
    path = DEST / name
    if path.exists():
        raise FileExistsError(path)
    path.write_text('"""Migrated reviewed pilot computations; orchestration is in pipeline.py."""\nfrom __future__ import annotations\n' + header + '\n\n' + body)


def rewrite(value, mapping):
    if isinstance(value, dict):
        return {k: rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v, mapping) for v in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    common = (PILOT / 'tbm_risk_tools/common.py').read_text().replace('parents[1]', 'parents[2]')
    (DEST / 'common.py').write_text(common)
    module('source_text.py', 'from pathlib import Path\nimport re\nimport fitz\nfrom .common import *',
           extract('tbm_risk_tools/appendices.py', ['duration', 'span', 'complete_measure']) +
           extract('tbm_risk_tools/reference_workflow.py', ['spatial_relation', 'reference_condition', 'scoped_geology_text']))
    module('gas.py', 'from pathlib import Path\nimport numpy as np\nimport pandas as pd\nfrom .common import *',
           extract('tbm_risk_tools/gas.py', ['finite', 'truth', 'supports', 'time_weighted_windows', 'read_day']) +
           extract('tbm_risk_tools/standards.py', ['runs']) +
           extract('tbm_risk_tools/practical_rag.py', ['good_samples', 'attention_series']))
    module('plots.py', 'from pathlib import Path\nimport matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\nimport matplotlib.dates as mdates\nfrom matplotlib import font_manager\nimport numpy as np\nimport pandas as pd\nfrom .common import GASES\nfrom .gas import truth',
           extract('tbm_risk_tools/plots.py', ['setup', 'separated', 'daily']))
    tokenizer = extract('tbm_risk_pdf_rag.py', ['tokens']).replace('def tokens(', 'def _tokens(')
    original_tree = ast.parse((PILOT / 'tbm_risk_pdf_rag.py').read_text())
    original = (PILOT / 'tbm_risk_pdf_rag.py').read_text()
    patterns = '\n'.join(ast.get_source_segment(original, n) for n in original_tree.body
                         if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ['CHAIN', 'LATIN', 'CHINESE'] for t in n.targets))
    knowledge_original = (PILOT / 'tbm_risk_tools/knowledge.py').read_text()
    k_tree = ast.parse(knowledge_original)
    hazards = next(ast.get_source_segment(knowledge_original, n) for n in k_tree.body
                   if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'HAZARDS' for t in n.targets))
    module('knowledge.py', 'import re\nimport json\nimport sqlite3\nfrom pathlib import Path\nfrom .common import *',
           patterns + '\n' + hazards + '\n' + tokenizer + extract('tbm_risk_tools/knowledge.py', ['mentions', 'local_locations', 'search', 'match_measures']))
    module('matching.py', 'from pathlib import Path\nfrom .common import *\nfrom .knowledge import mentions, match_measures',
           extract('tbm_risk_tools/matching.py', ['fact_location', 'match_day']))
    refs = extract('tbm_risk_tools/practical_rag.py', ['HybridReferences', 'expanded_reference', 'eligible_reference', 'choose_references'])
    refs = refs.replace("def __init__(self, config, output):", "def __init__(self, config, output, resources):\n        self.resources = Path(resources)")
    refs = refs.replace('SAVED/', 'self.resources/')
    module('references.py', 'import importlib.metadata\nfrom pathlib import Path\nimport re\nimport numpy as np\nfrom .common import *\nfrom .source_text import complete_measure, reference_condition, spatial_relation\nfrom .knowledge import search, local_locations', refs)
    assembly = extract('experiments/integrated_daily_20261029/integration.py',
                       ['source_ok', 'covered_length', 'design_context', 'forecasts', 'coverage', 'aligned_sources', 'fact', 'assemble', 'reference_contexts'])
    assembly = assembly.replace('from tbm_risk_tools.knowledge import local_locations', 'from .knowledge import local_locations')
    module('assembly.py', 'from copy import deepcopy\nfrom pathlib import Path\nimport re\nfrom .common import *\nfrom .source_text import duration, span, scoped_geology_text\nfrom .schema import TITLES', assembly)
    module('writer.py', 'from copy import deepcopy\nfrom decimal import Decimal\nimport json\nimport re\nfrom .schema import TITLES\nMODEL = "deepseek-v4-flash"',
           extract('experiments/integrated_daily_20261029/writer.py', ['model_facts', 'numeric_tokens', 'validate_plan', 'validate_text', 'request']))
    module('model.py', 'from datetime import datetime, timezone\nfrom .common import save',
           extract('tbm_risk_tools/appendices.py', ['model_stage']))
    render_original = (PILOT / 'experiments/integrated_daily_20261029/render.py').read_text()
    tree = ast.parse(render_original)
    css = next(ast.get_source_segment(render_original, n) for n in tree.body
               if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CSS' for t in n.targets))
    rendering = extract('experiments/integrated_daily_20261029/render.py',
                        ['table', 'draw_curves', 'photos', 'mileage_figures', 'source_table', 'comparative_rows', 'run'])
    rendering = rendering.replace('def run():\n    results=[]\n    for day in DATES:\n        folder=OUT/day;', 'def render_report(folder):\n    results=[]\n    for day in [folder.name]:\n        ')
    rendering = rendering.replace("    save(OUT/'render_summary.json',results)", '    return results[0]')
    module('rendering.py', 'import html\nimport json\nfrom pathlib import Path\nimport re\nimport shutil\nimport fitz\nimport numpy as np\nimport pandas as pd\nfrom .common import *\nfrom .source_text import span\nfrom .schema import TITLES\nfrom .plots import setup, separated\nfrom tbm_process.reporting_v2.engineering_presentation import window_rows, FIELDS\nfrom tbm_process.reporting_v2.publication import displayed_activity\nfrom tbm_process.reporting_v2.rendering import make_pdf', css + '\n' + rendering)
    module('risk_geometry.py', 'import re', extract('experiments/source_risk_labels_20261029/refine.py', ['row_points']))
    RES.mkdir(parents=True, exist_ok=False)
    saved = PILOT / 'outputs/full_329_checked'
    mapping = {}
    con = sqlite3.connect((saved / 'engineering_sources.sqlite').as_uri() + '?mode=ro', uri=True)
    docs = con.execute('SELECT path,sha256 FROM documents').fetchall()
    con.close()
    for path, digest in docs:
        source = Path(path)
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('resource_source_changed:' + path)
        target = RES / 'sources' / source.name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source, target)
        mapping[path] = str(target)
    shutil.copy2(saved / 'engineering_sources.sqlite', RES / 'engineering_sources.sqlite')
    con = sqlite3.connect(RES / 'engineering_sources.sqlite')
    for old, new in mapping.items():
        con.execute('UPDATE documents SET path=? WHERE path=?', (new, old))
    con.commit(); con.close()
    for name in ['measure_candidates.json', 'design_intervals.json', 'risk_records.json']:
        obj = rewrite(json.loads((saved / name).read_text()), mapping)
        (RES / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    for name in ['local_reference_vectors.npz', 'embedding_metadata.json']:
        shutil.copy2(PILOT / 'outputs/practical_rag_20261029' / name, RES / name)
    shutil.copy2(RES / 'measure_candidates.json', RES / 'reference_index_records.json')
    risk = PILOT / 'outputs/source_risk_labels_20261029_r2'
    for name in ['PDF_risk_grade_rows.refined.json', 'PDF_explicit_risk_points.refined.json', 'workbook_risk_labels.json', 'source_documents.time_policy_input.json', 'conditional_design_references.json']:
        obj = rewrite(json.loads((risk / name).read_text()), mapping)
        (RES / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    shutil.copy2(PILOT / 'configs/practical_rag.json', ROOT / 'configs/engineering_context.json')
    cache = ROOT / '.cache/local_embedding_models'
    cache.parent.mkdir(exist_ok=True)
    shutil.copytree(PILOT / '.cache/local_embedding_models', cache, dirs_exist_ok=True)
    (RES / 'migration_manifest.json').write_text(json.dumps({'origins': ORIGINS, 'source_path_relocation': mapping, 'source_content_unchanged': True}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
