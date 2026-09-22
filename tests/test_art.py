"""Art for hand-recorded printings (registry/art.py): only for printings
recorded by hand, never with metadata, the publisher's image always wins,
and rendering records a face in the same shape as the publisher's."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from registry import images as images_module
from registry.art import (check_art, check_held_art, metadata, plan_render, render_one,
                          source_name, strip)
from registry.images import RENDITION_RECIPE, object_name

MANUAL = {"source": "Store kit card, photographed", "recorded": "2026-09-23",
          "confirmed_at": None, "withdrawn": None}
EXPORT = {"printings": [
    {"printing_id": "P000001", "manual": None, "back": None},              # official
    {"printing_id": "P001700", "manual": MANUAL, "back": None},            # by hand
    {"printing_id": "P001701", "manual": MANUAL, "back": {"artist": "A"}},  # by hand, two faces
]}


def picture(fmt="PNG", size=(40, 56), exif_orientation=None, text=None):
    image = Image.new("RGB", size, (120, 60, 30))
    buffer = io.BytesIO()
    options = {}
    if exif_orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        exif[0x8825] = {1: "N"}  # a GPS block, as a phone would write
        options["exif"] = exif
    if text is not None:
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", text)
        options["pnginfo"] = info
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


class ArtDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.art = Path(self.tmp.name) / "art"
        self.art.mkdir()

    def put(self, name, data):
        (self.art / name).write_bytes(data)
        return self.art / name

    def errors(self):
        errors = []
        check_art(EXPORT, self.art, errors)
        return errors


class CheckTest(ArtDirTest):
    def test_clean_art_for_a_hand_recorded_printing_passes(self):
        self.put("P001700.png", picture())
        self.put("P001701.jpg", picture("JPEG"))
        self.put("P001701-r.webp", picture("WEBP"))
        self.assertEqual(self.errors(), [])

    def test_each_mistake_is_named(self):
        cases = {
            "cover.png": "is named",
            "P009999.png": "no printing P009999",
            "P000001.png": "official printing",
            "P001700-r.png": "has no back face",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                path = self.put(name, picture())
                errors = self.errors()
                self.assertEqual(len(errors), 1, errors)
                self.assertIn(expected, errors[0])
                path.unlink()

    def test_a_file_whose_name_lies_about_its_format_is_refused(self):
        self.put("P001700.jpg", picture("PNG"))
        self.assertIn("the file is PNG", self.errors()[0])

    def test_metadata_is_refused_and_strip_is_named_as_the_fix(self):
        self.put("P001700.jpg", picture("JPEG", exif_orientation=1))
        self.put("P001701.png", picture(text="taken at home"))
        errors = self.errors()
        self.assertEqual(len(errors), 2)
        self.assertIn("carries EXIF", errors[0])
        self.assertIn("python -m registry.art strip art/P001700.jpg", errors[0])
        self.assertIn("carries text", errors[1])

    def test_an_unreadable_file_is_an_error_not_a_crash(self):
        self.put("P001700.png", b"not an image")
        self.assertIn("not a readable image", self.errors()[0])

    def test_the_committed_art_passes(self):
        root = Path(__file__).resolve().parent.parent
        export = json.loads((root / "export" / "registry.json").read_text(encoding="utf-8"))
        errors = []
        check_art(export, root / "art", errors)
        self.assertEqual(errors, [])


class StripTest(unittest.TestCase):
    def test_strip_removes_exif_and_turns_the_pixels_upright(self):
        # Orientation 6: the camera held sideways; the stored pixels are
        # 56x40 and a viewer rotates them to 40x56.
        data = picture("JPEG", size=(56, 40), exif_orientation=6)
        self.assertEqual(metadata(data), ["EXIF"])
        clean = strip(data)
        self.assertEqual(metadata(clean), [])
        with Image.open(io.BytesIO(clean)) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (40, 56)))

    def test_strip_keeps_png_lossless_and_drops_text(self):
        data = picture(text="where")
        clean = strip(data)
        self.assertEqual(metadata(clean), [])
        with Image.open(io.BytesIO(clean)) as a, Image.open(io.BytesIO(data)) as b:
            self.assertEqual(a.format, "PNG")
            self.assertEqual(a.convert("RGB").tobytes(), b.convert("RGB").tobytes())


class RenderTest(ArtDirTest):
    def state(self):
        return {"recipe": RENDITION_RECIPE, "printings": {}}

    def test_render_writes_the_publishers_renditions_and_records_the_art_as_source(self):
        path = self.put("P001700.png", picture(size=(744, 1039)))
        state, work = self.state(), Path(self.tmp.name) / "work"
        key = render_one(path, "P001700", "front", work, state, today="2026-09-23")
        held = state["printings"]["P001700"]["front"]
        self.assertEqual(held["source"], "art/P001700.png")
        self.assertNotIn("drive_id", held)
        self.assertEqual((held["width"], held["height"], held["lowres"]), (744, 1039, False))
        self.assertEqual(set(held["objects"]), {"small", "normal", "large", "original"})
        self.assertTrue((work / object_name("P001700", key, "front", "original", "png")).exists())
        self.assertTrue((work / object_name("P001700", key, "front", "normal", "webp")).exists())

    def test_the_plan_renders_new_or_changed_art_only(self):
        path = self.put("P001700.png", picture())
        state = self.state()
        to_render, to_drop, left = plan_render(EXPORT, state, self.art)
        self.assertEqual([(p.name, pid, face) for p, pid, face in to_render],
                         [("P001700.png", "P001700", "front")])
        render_one(path, "P001700", "front", Path(self.tmp.name) / "work", state)
        self.assertEqual(plan_render(EXPORT, state, self.art), ([], [], []))
        # New art under the same name is a new image.
        self.put("P001700.png", picture(size=(41, 56)))
        self.assertEqual(len(plan_render(EXPORT, state, self.art)[0]), 1)

    def test_the_publishers_image_wins(self):
        self.put("P001700.png", picture())
        state = self.state()
        state["printings"]["P001700"] = {"front": {"key": "abc", "drive_id": "d", "source": "999-x.png"}}
        to_render, to_drop, left = plan_render(EXPORT, state, self.art)
        self.assertEqual((to_render, to_drop), ([], []))
        self.assertEqual(left[0][1], "the publisher's image is held")

    def test_art_that_is_gone_is_dropped(self):
        state = self.state()
        state["printings"]["P001700"] = {"front": {"key": "abc", "source": source_name("P001700.png")}}
        state["printings"]["P000001"] = {"front": {"key": "def", "drive_id": "d", "source": "001-x.png"}}
        self.assertEqual(plan_render(EXPORT, state, self.art), ([], [("P001700", "front")], []))


class HeldArtTest(unittest.TestCase):
    def test_held_art_on_an_official_printing_is_refused(self):
        errors = []
        check_held_art(EXPORT, {"printings": {
            "P000001": {"front": {"key": "a", "source": "art/P000001.png"}},
            "P001700": {"front": {"key": "b", "source": "art/P001700.png"}},
        }}, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("P000001 front", errors[0])


class VerifyWorkTest(unittest.TestCase):
    def test_verify_with_a_work_directory_checks_only_what_it_rendered(self):
        state = {"recipe": 1, "printings": {
            "P001700": {"front": {"key": "aaaaaaaaaaaa", "original_ext": "png",
                                  "objects": {"small": 1, "original": 2}}},
            "P000001": {"front": {"key": "bbbbbbbbbbbb", "original_ext": "png",
                                  "objects": {"small": 3}}},
        }}
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "P001700.aaaaaaaaaaaa.small.webp").write_bytes(b"x")
            (work / "P001700.aaaaaaaaaaaa.original.png").write_bytes(b"xx")
            args = mock.Mock(images="unused", work=str(work))
            with mock.patch.object(images_module, "load_images", return_value=state), \
                    mock.patch.object(images_module, "verify_objects", return_value=[]) as verify:
                self.assertEqual(images_module.cmd_verify(args), 0)
        self.assertEqual(sorted(verify.call_args[0][0]),
                         [("P001700.aaaaaaaaaaaa.original.png", 2),
                          ("P001700.aaaaaaaaaaaa.small.webp", 1)])


if __name__ == "__main__":
    unittest.main()
