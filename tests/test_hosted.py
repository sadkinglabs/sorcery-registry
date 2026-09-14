"""The pure parts of the release workflow's hosted-side steps."""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from registry.hosted import _status, find_rule, image_urls_in, redirect_rule, should_flip


class HostedTest(unittest.TestCase):
    def test_every_referenced_image_is_collected_once(self):
        urls = {"small": "https://i/x.small.webp", "original": "https://i/x.original.png"}
        export = {
            "cards": [{"image_urls": urls}, {"image_urls": None}],
            "printings": [{"image_urls": urls, "back": {"image_urls": {"small": "https://i/b.webp"}}},
                          {"image_urls": None, "back": None}],
        }
        self.assertEqual(image_urls_in(export),
                         ["https://i/b.webp", "https://i/x.original.png", "https://i/x.small.webp"])
        self.assertEqual(image_urls_in({"cards": [], "printings": []}), [])

    def test_alias_only_moves_to_the_newest_of_its_major(self):
        doc = {"latest": {"v3": "v3.2.0"}}
        self.assertTrue(should_flip(doc, "v3", "v3.2.0"))
        self.assertFalse(should_flip(doc, "v3", "v3.1.0"))   # an older re-run
        self.assertFalse(should_flip(doc, "v4", "v4.0.0"))   # not listed at all
        self.assertFalse(should_flip({}, "v3", "v3.2.0"))

    def test_alias_rule_is_found_by_id_or_by_its_dashboard_name(self):
        rules = [{"id": "aaa", "description": "www redirect"},
                 {"id": "bbb", "description": " V3 Alias "},
                 {"id": "ccc", "description": "v4 alias"}]
        self.assertEqual(find_rule(rules, "v3")["id"], "bbb")
        self.assertEqual(find_rule(rules, "v4")["id"], "ccc")
        self.assertEqual(find_rule(rules, "v3", "aaa")["id"], "aaa")
        self.assertIsNone(find_rule(rules, "v5"))
        self.assertIsNone(find_rule(rules, "v3", "zzz"))
        self.assertEqual(find_rule(rules, "v3", "")["id"], "bbb")  # an empty variable

    def test_redirect_rule_keeps_the_path_after_the_major(self):
        rule = redirect_rule("https://api.kairosarchive.net/", "v3", "v3.1.0")
        self.assertEqual(rule["expression"],
                         '(http.host eq "api.kairosarchive.net" and '
                         'starts_with(http.request.uri.path, "/v3/"))')
        target = rule["action_parameters"]["from_value"]["target_url"]["expression"]
        # "/v3/cards/C1.json"[3:] == "/cards/C1.json": the major plus its slash.
        self.assertEqual(target, 'concat("https://api.kairosarchive.net/v3.1.0", '
                                 'substring(http.request.uri.path, 3))')
        self.assertEqual(rule["action_parameters"]["from_value"]["status_code"], 302)
        self.assertTrue(rule["enabled"])
        longer = redirect_rule("https://api.kairosarchive.net", "v10", "v10.0.0")
        self.assertIn("substring(http.request.uri.path, 4)",
                      longer["action_parameters"]["from_value"]["target_url"]["expression"])


class _AliasHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/v3/"):
            self.send_response(302)
            self.send_header("Location", "http://%s:%d/v3.1.0%s" % (
                *self.server.server_address, self.path[3:]))
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class ProbeTest(unittest.TestCase):
    """The alias probe must see the redirect itself, not where it leads:
    urlopen follows 3xx by default, which once made a working alias look
    broken (a 200 from the target, no Location)."""

    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), _AliasHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://%s:%d" % self.server.server_address

    def tearDown(self):
        self.server.shutdown()

    def test_probe_reports_the_redirect_not_its_target(self):
        status, headers = _status(f"{self.base}/v3/index.json", method="GET",
                                  follow_redirects=False)
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], f"{self.base}/v3.1.0/index.json")
        status, headers = _status(f"{self.base}/v3/index.json", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")


if __name__ == "__main__":
    unittest.main()
