"""Query one geological database without rereading or parsing source PDFs."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tbm_process.geology_store.database import GeologyStore

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--as-of")
    p.add_argument("--coverage", action="store_true")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    db = GeologyStore(args.database)
    result = db.query_geological_coverage(args.start, args.end, as_of=args.as_of) if args.coverage else db.query_geology_by_chainage(args.start, args.end, as_of=args.as_of)
    data = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(data+"\n")
    else:
        print(data)
    db.close()
