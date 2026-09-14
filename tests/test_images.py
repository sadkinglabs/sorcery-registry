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
                         {"P000003/front": ["004-witch-b-s.png", "004-witch-b-s.jpg"]})
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


class DecisionsAndFacesTest(unittest.TestCase):
    EXPORT = {
        "printings": [
            {"printing_id": "P000001", "slug": "001-apprentice-wizard-b-s", "back": None},
            {"printing_id": "P000009", "slug": "004-druid-bt-s", "back": {"artist": "x"}},
            {"printing_id": "P000010", "slug": "004-foot_soldier-bt-s", "back": {"artist": "y"}},
        ],
        "slug_history": [
            {"slug": "001-apprentice-wizard-b-s", "printing_id": "P000001"},
            {"slug": "004-druid-bt-s", "printing_id": "P000009"},
            {"slug": "004-foot_soldier-bt-s", "printing_id": "P000010"},
        ],
    }

    def test_r_suffix_is_the_back_face_only_when_the_printing_has_one(self):
        files = [{"name": "004-druid-bt-s.png", "path": ""},
                 {"name": "004-druid-bt-s-r.png", "path": ""},
                 {"name": "001-apprentice-wizard-b-s-r.png", "path": ""}]
        mapping = map_listing(files, self.EXPORT)
        faces = {(m["printing_id"], m["face"]): m["name"] for m in mapping["mapped"]}
        self.assertEqual(faces[("P000009", "front")], "004-druid-bt-s.png")
        self.assertEqual(faces[("P000009", "back")], "004-druid-bt-s-r.png")
        self.assertEqual([u["name"] for u in mapping["unmapped"]],
                         ["001-apprentice-wizard-b-s-r.png"])
        self.assertEqual(mapping["printings_without_file"], ["P000001", "P000010"])
        self.assertEqual(mapping["backs_without_file"], ["P000010"])

    def test_decisions_assign_and_ignore_with_reasons(self):
        decisions = {"assign": {"004-foot_soldiers-bt-s.png": {"printing_id": "P000010", "face": "front", "reason": "plural"},
                                "004-foot_soldiers-bt-s-r.png": {"printing_id": "P000010", "face": "back", "reason": "plural"}},
                     "ignore": {"004-foot_soldiers_english-bt-s.png": "same bytes"}}
        files = [{"name": n, "path": ""} for n in ("004-foot_soldiers-bt-s.png", "004-foot_soldiers-bt-s-r.png",
                                                   "004-foot_soldiers_english-bt-s.png")]
        mapping = map_listing(files, self.EXPORT, decisions)
        faces = {(m["printing_id"], m["face"]): m for m in mapping["mapped"]}
        self.assertEqual(faces[("P000010", "front")]["decided"], "plural")
        self.assertEqual(faces[("P000010", "back")]["name"], "004-foot_soldiers-bt-s-r.png")
        self.assertEqual([i["name"] for i in mapping["ignored"]], ["004-foot_soldiers_english-bt-s.png"])
        self.assertEqual(mapping["unmapped"], [])
        self.assertEqual(mapping["backs_without_file"], ["P000009"])

    def test_the_committed_decisions_file_is_well_formed(self):
        from registry.images import load_decisions
        decisions = load_decisions()
        self.assertIn("004-foot_soldiers-bt-s.png", decisions["assign"])
        for entry in decisions["assign"].values():
            self.assertTrue(entry["reason"])

    def test_a_decision_without_a_reason_is_refused(self):
        import json, tempfile
        from pathlib import Path
        from registry.images import load_decisions
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.json"
            path.write_text(json.dumps({"assign": {"x.png": {"printing_id": "P1", "face": "front"}}}))
            with self.assertRaises(ValueError):
                load_decisions(path)


def _png(width, height):
    from PIL import Image
    import io
    image = Image.new("RGB", (width, height), (120, 40, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class RenditionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import PIL  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Pillow not installed")

    def test_hires_source_renders_scryfall_sizes_and_is_not_lowres(self):
        from PIL import Image
        import io
        from registry.images import is_lowres, render
        renditions, (w, h) = render(_png(744, 1039))
        self.assertEqual((w, h), (744, 1039))
        self.assertFalse(is_lowres(w))
        sizes = {name: (wd, ht) for name, (_, wd, ht) in renditions.items()}
        self.assertEqual(sizes["small"][1], 204)
        self.assertEqual(sizes["normal"][1], 680)
        self.assertEqual(sizes["large"][1], 936)
        for name, (data, wd, ht) in renditions.items():
            self.assertLessEqual(wd, dict((n, bw) for n, bw, _ in __import__("registry.images", fromlist=["RENDITIONS"]).RENDITIONS)[name])
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.size, (wd, ht))

    def test_lowres_source_is_upscaled_and_flagged(self):
        from registry.images import is_lowres, render
        renditions, (w, h) = render(_png(380, 531))
        self.assertTrue(is_lowres(w))
        self.assertEqual(renditions["large"][2], 936)   # upscaled, the owner's decision
        self.assertEqual(renditions["small"][2], 204)

    def test_art_key_changes_with_the_bytes_and_with_the_recipe(self):
        from registry.images import art_key
        a, b = _png(10, 14), _png(10, 15)
        self.assertEqual(len(art_key(a)), 12)
        self.assertNotEqual(art_key(a), art_key(b))
        self.assertNotEqual(art_key(a, recipe=1), art_key(a, recipe=2))
        self.assertEqual(art_key(a), art_key(a))

    def test_object_names_are_self_describing(self):
        from registry.images import object_name
        self.assertEqual(object_name("P000937", "ab12cd34ef56", "front", "normal", "webp"),
                         "P000937.ab12cd34ef56.normal.webp")
        self.assertEqual(object_name("P000937", "ab12cd34ef56", "back", "original", "png"),
                         "P000937.ab12cd34ef56.back.original.png")


class FetchStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import PIL  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("Pillow not installed")

    def test_fetch_one_writes_objects_and_records_the_source(self):
        import hashlib, tempfile
        from pathlib import Path
        from registry.images import RENDITION_RECIPE, fetch_one, image_objects, load_images, plan_fetch, save_images
        original = _png(744, 1039)
        entry = {"id": "drive1", "name": "006-witch-b-s.png", "md5": hashlib.md5(original).hexdigest(),
                 "printing_id": "P000005", "face": "front"}
        with tempfile.TemporaryDirectory() as tmp:
            state = {"recipe": RENDITION_RECIPE, "printings": {}}
            key = fetch_one(entry, "KEY", Path(tmp) / "work", state,
                            get_bytes=lambda url: original, today="2026-09-14")
            held = state["printings"]["P000005"]["front"]
            self.assertEqual(held["key"], key)
            self.assertEqual(held["md5"], entry["md5"])
            self.assertEqual((held["width"], held["height"]), (744, 1039))
            self.assertFalse(held["lowres"])
            self.assertEqual(held["original_ext"], "png")
            self.assertEqual(set(held["objects"]), {"original", "small", "normal", "large"})
            names = sorted(p.name for p in (Path(tmp) / "work").iterdir())
            self.assertEqual(names, sorted(n for n, _ in image_objects(state)))
            self.assertIn(f"P000005.{key}.original.png", names)
            # Persisted and reloaded, the same file is not fetched again;
            # a changed MD5 or recipe is.
            save_images(state, Path(tmp) / "images.json")
            reloaded = load_images(Path(tmp) / "images.json")
            mapping = {"mapped": [entry]}
            self.assertEqual(plan_fetch(mapping, reloaded), [])
            changed = dict(entry, md5="different")
            self.assertEqual(plan_fetch({"mapped": [changed]}, reloaded), [changed])
            reloaded["recipe"] = RENDITION_RECIPE + 1
            self.assertEqual(plan_fetch(mapping, reloaded), [entry])

    def test_a_download_that_does_not_match_the_listing_is_refused(self):
        import tempfile
        from pathlib import Path
        from registry.images import RENDITION_RECIPE, fetch_one
        entry = {"id": "drive1", "name": "x.png", "md5": "not-it", "printing_id": "P1", "face": "front"}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                fetch_one(entry, "KEY", Path(tmp), {"recipe": RENDITION_RECIPE, "printings": {}},
                          get_bytes=lambda url: _png(10, 14))
