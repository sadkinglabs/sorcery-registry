"""scripts/fetch_versions.sh is the release workflow's read of the
discovery document. These tests run it against a stub `aws` that plays
each way the read can go, and check that only two outcomes let the
workflow continue: a valid document, or a confirmed absence with
nothing released yet."""

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fetch_versions.sh"

GOOD = json.dumps({"base_url": "https://api.test", "latest": {"v3": "v3.3.3"},
                   "releases": [{"tag": "v3.3.3", "schema_version": 11, "released_at": "2026-09-16", "sha256": "b" * 64}]})

# The stub answers `aws s3 cp` and `aws s3 ls` according to MODE.
STUB = r'''#!/usr/bin/env bash
mode="$FAKE_AWS_MODE"
if [ "$2" = "cp" ]; then
  case "$mode" in
    ok) printf '%s' "$FAKE_BODY" > "$4"; exit 0;;
    malformed) printf '<html>blocked</html>' > "$4"; exit 0;;
    empty) : > "$4"; exit 0;;
    404|404-released|404-listfail) echo 'fatal error: An error occurred (404) when calling the HeadObject operation: Key "versions.json" does not exist' >&2; exit 1;;
    403) echo 'fatal error: An error occurred (403) when calling the HeadObject operation: Forbidden' >&2; exit 1;;
    net) echo 'fatal error: Could not connect to the endpoint URL: "https://x.r2.cloudflarestorage.com/"' >&2; exit 1;;
  esac
fi
if [ "$2" = "ls" ]; then
  case "$3" in
    s3://*/) [ "$mode" = "404-listfail" ] && { echo "fatal error: Could not connect" >&2; exit 1; }
             echo "                           PRE v3.3.2/"; echo "                           PRE v3.3.3/"; exit 0;;
    *RELEASED) [ "$mode" = "404-released" ] && [[ "$3" == *v3.3.2/RELEASED ]] && { echo "2026-09-16 10:00:00 90 RELEASED"; exit 0; }; exit 1;;
  esac
fi
echo "unexpected: $*" >&2; exit 99
'''


class FetchVersionsTest(unittest.TestCase):
    def run_script(self, mode, body=GOOD, tag="v3.3.3"):
        tmp = Path(tempfile.mkdtemp())
        stub = tmp / "aws"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        out = tmp / "previous.json"
        env = dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}", FAKE_AWS_MODE=mode, FAKE_BODY=body)
        r = subprocess.run(["bash", str(SCRIPT), "bucket", tag, str(out)], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        return r, out

    def test_a_valid_document_is_written(self):
        r, out = self.run_script("ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(out.read_text())["latest"], {"v3": "v3.3.3"})

    def test_a_confirmed_404_with_nothing_released_is_the_first_release(self):
        r, out = self.run_script("404")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(out.exists())
        self.assertIn("first release", r.stdout)

    def test_a_404_beside_a_released_root_stops_the_run(self):
        r, out = self.run_script("404-released")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(out.exists())
        self.assertIn("RELEASED exists", r.stdout)

    def test_network_auth_and_listing_failures_stop_the_run(self):
        for mode in ("403", "net", "404-listfail"):
            with self.subTest(mode=mode):
                r, out = self.run_script(mode)
                self.assertEqual(r.returncode, 1, mode)
                self.assertFalse(out.exists(), mode)
                self.assertIn("::error::", r.stdout, mode)

    def test_a_malformed_or_empty_download_stops_the_run(self):
        for mode in ("malformed", "empty"):
            with self.subTest(mode=mode):
                r, _ = self.run_script(mode)
                self.assertEqual(r.returncode, 1, mode)
                self.assertIn("not a valid discovery document", r.stdout, mode)


if __name__ == "__main__":
    unittest.main()
