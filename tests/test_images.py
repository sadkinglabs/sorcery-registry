"""Listing the publisher's image folder: paging and recursion over the
Drive API (with a fake), mapping files to printings through every slug
ever issued, and the summary a human reads before anything is fetched."""

import unittest
import urllib.parse

from registry.images import (DRIVE_FILES, FOLDER_MIME, _redact, file_key, list_folder,
                             map_listing, render_summary, summarize)

EXPORT = {
    "printings": [
        {"printing_id": "P000001", "slug": "001-apprentice-wizard-b-s"},
        {"printing_id": "P000002", "slug": "001-apprentice-wizard-b-f"},
        {"printing_id": "P000003", "slug": "004-witch-b-s"},
    ],
    "slug_history": [
        {"slug": "001-apprentice_wizard-b-s", "printing_id": "P000001",
         "valid_from": "2026-08-19", "valid_to": "2026-08-20"},
        {"slug": "001-apprentice-wizard-b-s", "printing_id": "P000001",
         "valid_from": "2026-08-20", "valid_to": None},
        {"slug": "001-apprentice-wizard-b-f", "printing_id": "P000002",
         "valid_from": "2026-08-19", "valid_to": None},
        {"slug": "004-witch-b-s", "printing_id": "P000003",
         "valid_from": "2026-08-19", "valid_to": None},
    ],
}


def _file(name, folder_id="root", **extra):
    entry = {"id": f"id-{name}", "name": name, "mimeType": "image/png",
             "md5Checksum": "m" + name, "size": "1000", "modifiedTime": "2026-01-01T00:00:00Z",
             "imageMediaMetadata": {"width": 750, "height": 1050}}
    entry.update(extra)
    return entry


class FakeDrive:
    """Answers files.list per folder, in pages, and records every call."""

    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        folder = query["q"][0].split("'")[1]
        page = int(query.get("pageToken", ["0"])[0])
        entries = self.tree[folder]
        chunk, rest = entries[page * 2:(page + 1) * 2], entries[(page + 1) * 2:]
        answer = {"files": chunk}
        if rest:
            answer["nextPageToken"] = str(page + 1)
        return answer


class ListingTest(unittest.TestCase):
    def test_pages_and_subfolders_are_walked_sequentially(self):
        drive = FakeDrive({
            "root": [_file("001-apprentice-wizard-b-s.png"), _file("004-witch-b-s.png"),
                     {"id": "sub", "name": "Backs", "mimeType": FOLDER_MIME}],
            "sub": [_file("001-apprentice-wizard-b-f.png")],
        })
        files = list_folder("root", "SECRET", get=drive, pause=0)
        self.assertEqual([(f["path"], f["name"]) for f in files], [
            ("", "001-apprentice-wizard-b-s.png"), ("", "004-witch-b-s.png"),
            ("Backs/", "001-apprentice-wizard-b-f.png")])
        self.assertEqual(files[0]["width"], 750)
        self.assertEqual(files[0]["size"], 1000)
        self.assertEqual(files[0]["md5"], "m001-apprentice-wizard-b-s.png")
        # root needed two pages (3 entries, 2 per page), the subfolder one.
        self.assertEqual(len(drive.calls), 3)
        for url in drive.calls:
            self.assertTrue(url.startswith(DRIVE_FILES))
            self.assertIn("key=SECRET", url)
            self.assertIn("trashed+%3D+false", url)

    def test_the_key_is_redacted_from_anything_that_could_be_printed(self):
        self.assertEqual(_redact("https://x/files?q=a&key=SECRET&fields=b"),
                         "https://x/files?q=a&key=REDACTED&fields=b")


class MappingTest(unittest.TestCase):
    def test_files_map_through_any_slug_ever_issued(self):
        files = [
            {"name": "001-apprentice_wizard-b-s.png", "path": ""},   # superseded slug
            {"name": "001-Apprentice-Wizard-B-F.PNG", "path": ""},   # case-insensitive
            {"name": "004-witch-b-s.png", "path": ""},
            {"name": "004-witch-b-s.jpg", "path": "dupes/"},          # second claim
            {"name": "card-back.png", "path": ""},                    # not a slug
        ]
        mapping = map_listing(files, EXPORT)
        by_name = {m["name"]: m for m in mapping["mapped"]}
        self.assertEqual(by_name["001-apprentice_wizard-b-s.png"]["printing_id"], "P000001")
        self.assertFalse(by_name["001-apprentice_wizard-b-s.png"]["slug_is_current"])
        self.assertEqual(by_name["001-Apprentice-Wizard-B-F.PNG"]["printing_id"], "P000002")
        self.assertTrue(by_name["001-Apprentice-Wizard-B-F.PNG"]["slug_is_current"])
        self.assertEqual([u["name"] for u in mapping["unmapped"]], ["card-back.png"])
        self.assertEqual(mapping["duplicates"],
                         {"P000003": ["004-witch-b-s.png", "004-witch-b-s.jpg"]})
        self.assertEqual(mapping["printings_without_file"], [])

    def test_missing_printings_are_listed(self):
        mapping = map_listing([{"name": "004-witch-b-s.png", "path": ""}], EXPORT)
        self.assertEqual(mapping["printings_without_file"], ["P000001", "P000002"])

    def test_file_key(self):
        self.assertEqual(file_key("004-witch-b-s.png"), "004-witch-b-s")
        self.assertEqual(file_key(" 004-WITCH-b-s.tar.gz "), "004-witch-b-s.tar")
        self.assertEqual(file_key("noext"), "noext")


class SummaryTest(unittest.TestCase):
    def test_summary_counts_what_a_reviewer_needs(self):
        files = [
            {"name": "a.png", "path": "", "mime_type": "image/png", "size": 100,
             "width": 750, "height": 1050},
            {"name": "b.jpg", "path": "x/", "mime_type": "image/jpeg", "size": 300,
             "width": 750, "height": 1050},
            {"name": "c", "path": "", "mime_type": "image/png", "size": None,
             "width": None, "height": None},
        ]
        mapping = {"mapped": [{"slug_is_current": False}], "unmapped": files[1:],
                   "duplicates": {}, "printings_without_file": ["P000009"]}
        summary = summarize(files, mapping)
        self.assertEqual(summary["files"], 3)
        self.assertEqual(summary["folders"], {"/": 2, "x/": 1})
        self.assertEqual(summary["extensions"], {"png": 1, "jpg": 1, "": 1})
        self.assertEqual(summary["dimensions"], {"750x1050": 2})
        self.assertEqual(summary["bytes_total"], 400)
        self.assertEqual(summary["mapped_to_superseded_slug"], 1)
        self.assertEqual(summary["unmapped_examples"], ["x/b.jpg", "c"])
        text = render_summary(summary)
        self.assertIn("files: 3 in 2 folder(s)", text)
        self.assertIn("printings without a file: 1", text)


if __name__ == "__main__":
    unittest.main()
