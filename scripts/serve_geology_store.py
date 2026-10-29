"""Serve the read-only local geological evidence browser; no external API calls."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tbm_process.geology_store.server import serve

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--port", type=int, default=8766)
    args = p.parse_args()
    serve(args.database, args.port)
