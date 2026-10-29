# Three-Chapter Report Adapter

For the current fixed engineering publication layout, use `scripts/run_three_chapter_report.py publish` over a completed single-day bundle. See `docs/ENGINEERING_REPORT_STANDARD.md`. The research stages below retain their original experiment layouts and are not themselves the accepted publication standard.

The experimental three-chapter route is opt-in. Existing six-chapter defaults and frozen PLC phase/state algorithms are unchanged.

```bash
python scripts/run_three_chapter_report.py prepare --output /new/output --dates 2024-04-26 --database /local/geology.sqlite --analysis-dir /local/frozen/analysis
```

Preparation does not read credentials or call an LLM. It exports time-admissible geology, builds the existing facts and response features, preserves each geological source's own interval resolution, and renders clearly labelled deterministic previews. Existing registry and source-root paths must be rebound for another machine.

`scripts/run_engineering_report_v2.py --layout three` also dispatches to this adapter. The unchanged default is `six`.

Source modules:

- `evidence_graph/geology_response.py`: geological source interval / qualified stable PLC correspondence. No causal interpretation or statistical-independence claim.
- `evidence_graph/three_chapter.py`: process summary, evidence selection, structured paragraph requests, explicit plan evidence closure, and targeted consistency checks.
- `evidence_graph/joint_reporting.py`: existing bounded API session, planner/generator checks, and local revision with retention checks.
- `reporting_v2/three_chapter_rendering.py`: shared HTML/PDF layout and real-data charts.

The October 27 pilot retained original failures, explicit interface recovery, program-compiled endpoint closure and subsequent revisions as separate artifacts. After additional user authorization, actual total calls were 10. One B report passed program and PDF checks after one accepted paragraph change; A remained failed. This is development evidence, not an independent controlled efficacy result. Semantic labels remain REVIEW and human labels are blank.

The special recovery CLI stages are bounded audit continuations for that recorded pilot, not an infinite production retry loop. Never execute paid stages merely to inspect the project. The API key is loaded by the existing secret loader and is never included in outputs.
