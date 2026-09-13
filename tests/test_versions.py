"""versions.json is what consumers trust to find a release; these tests
pin its three rules: semver order, latest only moves forward, a published
tag's digest never changes."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from registry.versions import add_release, latest_tag, parse_tag, render

BASE = "https://api.kairosarchive.net"


def add(doc, tag, sha="a" * 64, schema=9, date="2026-09-14"):
    return add_release(doc, tag, schema, date, sha, BASE if doc is None else None)


class VersionsTest(unittest.TestCase):
    def test_first_release_builds_the_document(self):
        doc = add(None, "v3.1.0")
        self.assertEqual(doc["base_url"], BASE)
        self.assertEqual(doc["latest"], {"v3": "v3.1.0"})
        self.assertEqual(doc["releases"], [{"tag": "v3.1.0", "schema_version": 9,
                                            "released_at": "2026-09-14", "sha256": "a" * 64}])
        with self.assertRaises(ValueError):
            add_release(None, "v3.1.0", 9, "2026-09-14", "a" * 64)  # no base_url

    def test_releases_are_ordered_by_semver_newest_first(self):
        doc = add(None, "v3.2.0")
        doc = add(doc, "v3.10.0", sha="b" * 64)
        doc = add(doc, "v3.9.1", sha="c" * 64)
        self.assertEqual([r["tag"] for r in doc["releases"]], ["v3.10.0", "v3.9.1", "v3.2.0"])
        self.assertEqual(latest_tag(doc, "v3"), "v3.10.0")

    def test_latest_only_moves_forward(self):
        doc = add(None, "v3.1.0")
        # A patch to an older line, released later, must not demote latest.
        doc = add(doc, "v3.0.1", sha="b" * 64, date="2026-10-01")
        self.assertEqual(latest_tag(doc, "v3"), "v3.1.0")
        self.assertEqual([r["tag"] for r in doc["releases"]], ["v3.1.0", "v3.0.1"])

    def test_majors_are_tracked_separately(self):
        doc = add(None, "v3.1.0")
        doc = add(doc, "v4.0.0", sha="b" * 64, schema=10)
        doc = add(doc, "v3.2.0", sha="c" * 64)
        self.assertEqual(doc["latest"], {"v3": "v3.2.0", "v4": "v4.0.0"})
        self.assertEqual([r["tag"] for r in doc["releases"]], ["v4.0.0", "v3.2.0", "v3.1.0"])

    def test_re_adding_a_tag_is_a_no_op_and_a_changed_digest_is_refused(self):
        doc = add(None, "v3.1.0")
        again = add(doc, "v3.1.0")
        self.assertEqual(again, doc)
        with self.assertRaises(ValueError):
            add(doc, "v3.1.0", sha="f" * 64)

    def test_input_is_not_modified(self):
        doc = add(None, "v3.1.0")
        frozen = json.loads(json.dumps(doc))
        add(doc, "v3.2.0", sha="b" * 64)
        self.assertEqual(doc, frozen)

    def test_only_plain_release_tags_are_accepted(self):
        self.assertEqual(parse_tag("v3.10.2"), (3, 10, 2))
        for bad in ("3.1.0", "v3.1", "v3.1.0-rc1", "latest", "", None):
            with self.assertRaises(ValueError):
                parse_tag(bad)

    def test_cli_round_trips_through_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "registry.json"
            export.write_text(json.dumps({"header": {"schema_version": 9}}))
            out = Path(tmp) / "versions.json"
            base = ["python", "-m", "registry.versions", "--export", str(export), "--out", str(out)]
            root = Path(__file__).resolve().parent.parent
            run = lambda *extra: subprocess.run([sys.executable, *base[1:], *extra], cwd=root,
                                                capture_output=True, text=True, check=True)
            run("--add", "v3.1.0", "--sha256", "a" * 64, "--base-url", BASE + "/",
                "--released-at", "2026-09-14")
            first = json.loads(out.read_text())
            self.assertEqual(first["base_url"], BASE)
            self.assertEqual(first["releases"][0]["schema_version"], 9)
            # Second run reads the first document back; the empty-file case
            # (a fresh bucket answers nothing) is treated as "no previous".
            run("--add", "v3.2.0", "--sha256", "b" * 64, "--previous", str(out),
                "--released-at", "2026-11-01")
            second = json.loads(out.read_text())
            self.assertEqual(second["latest"], {"v3": "v3.2.0"})
            self.assertEqual(len(second["releases"]), 2)
            self.assertEqual(out.read_text(), render(second))
            empty = Path(tmp) / "empty.json"
            empty.write_text("")
            run("--add", "v3.0.0", "--sha256", "c" * 64, "--previous", str(empty),
                "--base-url", BASE)
            self.assertEqual(json.loads(out.read_text())["latest"], {"v3": "v3.0.0"})


if __name__ == "__main__":
    unittest.main()
