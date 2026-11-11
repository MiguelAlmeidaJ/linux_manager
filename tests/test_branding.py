import base64
import os
import struct
import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException
import branding


def png_fixture():
    # PNG header + IHDR dimensions; enough for signature validation.
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" +
            struct.pack(">II", 64, 64) + b"\x08\x06\x00\x00\x00")


class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.old_root = branding.ROOT
        branding.ROOT = Path(self.folder.name) / "branding"

    def tearDown(self):
        branding.ROOT = self.old_root
        self.folder.cleanup()

    def test_upload_and_reset_logo(self):
        content = base64.b64encode(png_fixture()).decode()
        result = branding.save("logo", branding.AssetUpload(
            filename="logo.png", content_base64=content))
        self.assertIn("/branding/logo?v=", result["logo"])
        self.assertIsNone(result["favicon"])
        path, mimetype = branding.asset("logo")
        self.assertEqual(mimetype, "image/png")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(branding.ROOT.stat().st_mode & 0o777, 0o700)
        result = branding.reset("logo")
        self.assertIsNone(result["logo"])
        with self.assertRaises(HTTPException) as error:
            branding.asset("logo")
        self.assertEqual(error.exception.status_code, 404)

    def test_reject_svg_html_and_wrong_image_signature(self):
        for name, content in [
            ("logo.svg", b"<svg onload=alert(1)>"),
            ("logo.png", b"<script>test</script>"),
            ("logo.png", b"\x89PNG\r\n\x1a\n"),
            ("logo.jpg", b"<html>hello</html>"),
        ]:
            with self.subTest(name=name, content=content), self.assertRaises(HTTPException):
                branding.save("logo", branding.AssetUpload(
                    filename=name, content_base64=base64.b64encode(content).decode()))

    def test_limit_size_and_unsupported_asset_type(self):
        content=base64.b64encode(png_fixture()).decode()
        with self.assertRaises(HTTPException):
            branding.save("something", branding.AssetUpload(filename="logo.png", content_base64=content))
        with self.assertRaises(HTTPException):
            branding.save("favicon", branding.AssetUpload(
                filename="favicon.jpeg", content_base64=content))
        with self.assertRaises(HTTPException):
            branding.save("logo", branding.AssetUpload(
                filename="logo.png", content_base64=base64.b64encode(png_fixture()+b"x"*1048576).decode()))

    def test_manifest_not_overwritten_on_rejected_upload(self):
        content=base64.b64encode(png_fixture()).decode()
        branding.save("logo", branding.AssetUpload(filename="valid.png", content_base64=content))
        before = branding.metadata()
        with self.assertRaises(HTTPException):
            branding.save("logo", branding.AssetUpload(filename="invalid.png",
                content_base64=base64.b64encode(b"malformed").decode()))
        self.assertEqual(branding.metadata(), before)


if __name__ == "__main__":
    unittest.main()
