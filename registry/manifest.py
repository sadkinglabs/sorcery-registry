"""Release manifest generator.

    python -m registry.manifest --dataset-version v1.2.0
    python -m registry.manifest --dataset-version v1.2.0 --out manifest.json

The manifest is generated at RELEASE time and attached to the GitHub
release. It is never committed: dataset_version changes with every
release, so a committed manifest would put a diff line on every release
and break the no-op determinism the rest of the repository relies on.
"""

import argparse
import hashlib
import json
from pathlib import Path

from .export import EXPORT_PATH, SCHEMA_PATH, checksum_path

DB_PATH = Path("registry.sqlite")


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_manifest(dataset_version, export_path, db_path, schema_path):
    export_path, db_path, schema_path = Path(export_path), Path(db_path), Path(schema_path)
    export = json.loads(export_path.read_text(encoding="utf-8"))
    header = export["header"]

    counts = {
        "sets": header["sets"],
        "cards": header["cards"],
        "printings": header["printings"],
        "slug_history": header["slug_history"],
        "name_history": header["name_history"],
    }

    artifacts = []
    for path in (export_path, db_path, schema_path):
        artifacts.append({
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_digest(path),
        })

    # The committed checksum must already agree with the export we are
    # about to publish; a mismatch means the release would ship a lie.
    sha_path = checksum_path(export_path)
    stated = sha_path.read_text(encoding="utf-8").split()[0]
    if stated != artifacts[0]["sha256"]:
        raise ValueError(f"{sha_path} does not match {export_path}; "
                         f"regenerate both with python -m registry.export")

    return {
        "dataset_version": dataset_version,
        "schema_version": header["schema_version"],
        "counts": counts,
        "artifacts": artifacts,
    }


def render(manifest):
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Build the release manifest.")
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--export", default=str(EXPORT_PATH))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--schema", default=str(SCHEMA_PATH))
    parser.add_argument("--out", help="write the manifest here instead of stdout")
    args = parser.parse_args()

    rendered = render(build_manifest(args.dataset_version, args.export,
                                     args.db, args.schema))
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
