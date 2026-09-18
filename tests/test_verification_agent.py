"""Image sweeps remain HEAD checks, attributed to the image workflow."""
import unittest
from unittest.mock import patch
from registry import hosted, images


class VerificationAgentTest(unittest.TestCase):
    def test_image_verifier_identifies_itself(self):
        with patch.object(hosted, "_status", return_value=(200, {})) as status:
            self.assertEqual(images.verify_objects([("one.webp", 1)], workers=1), [])
        status.assert_called_once_with(
            f"{images.IMAGE_BASE}/one.webp", headers={"User-Agent": images.USER_AGENT})

    def test_status_preserves_head_and_forwards_agent(self):
        with patch.object(hosted, "_request") as request:
            request.return_value.__enter__.return_value.status = 200
            request.return_value.__enter__.return_value.headers = {"Content-Length": "1"}
            self.assertEqual(hosted._status("https://example.test/x", headers={"User-Agent": "images"}),
                             (200, {"content-length": "1"}))
        request.assert_called_once_with("https://example.test/x", "HEAD", headers={"User-Agent": "images"}, follow_redirects=True)
