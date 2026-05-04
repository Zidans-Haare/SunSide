"""Download a GTFS feed and import it into a SQLite database.

Convenience wrapper around `scripts/gtfs_import.py`. Knows a couple of common
feeds by short name; arbitrary feeds via --url.

Examples:
    python scripts/fetch_gtfs.py flixbus
    python scripts/fetch_gtfs.py db
    python scripts/fetch_gtfs.py --url https://example.com/feed.zip --name myfeed

Idempotent: existing DB is overwritten. Existing zip in data/gtfs/<name>.zip is
re-used unless --force is given.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.gtfs_import import import_feed  # noqa: E402

PRESETS = {
    "flixbus": "https://gtfs.gis.flix.tech/gtfs_generic_eu.zip",
    "db": "https://download.gtfs.de/germany/free/latest.zip",
}


def download(url: str, dest: Path) -> None:
    print(f"[fetch] {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    started = time.time()
    with urllib.request.urlopen(url, timeout=600) as response, open(tmp, "wb") as out:  # noqa: S310
        shutil.copyfileobj(response, out, length=1024 * 1024)
    if tmp.stat().st_size < 1_000_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file suspiciously small (<1 MB): {url}")
    tmp.replace(dest)
    elapsed = time.time() - started
    size_mb = dest.stat().st_size / 1024 / 1024
    print(f"[fetch] done in {elapsed:.1f}s, {size_mb:.1f} MB -> {dest}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("preset", nargs="?", choices=sorted(PRESETS), help="Known feed shortcut")
    parser.add_argument("--url", help="Custom GTFS zip URL (overrides preset)")
    parser.add_argument("--name", help="Friendly feed name (default: preset name or derived from URL)")
    parser.add_argument("--force", action="store_true", help="Re-download even if zip already exists")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "gtfs", help="Where to store zip + sqlite")
    args = parser.parse_args()

    if not args.url and not args.preset:
        parser.error("Either a preset (e.g. 'flixbus') or --url is required.")
    url = args.url or PRESETS[args.preset]
    name = args.name or args.preset or Path(url).stem
    data_dir: Path = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    zip_path = data_dir / f"{name}.zip"
    db_path = data_dir / f"{name}.sqlite"

    if zip_path.exists() and not args.force:
        print(f"[fetch] reusing existing {zip_path} (use --force to refresh)")
    else:
        download(url, zip_path)

    import_feed(zip_path, db_path, feed_name=name)
    print(f"[fetch] feed '{name}' ready at {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
