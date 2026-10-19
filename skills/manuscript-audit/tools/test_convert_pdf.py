"""PDF conversion tests keep invented pages in temporary directories."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from convert_pdf import convert


class Page:
    def __init__(self, text):
        self.text = text

    def extract_text(self, **options):
        assert options["extraction_mode"] == "layout"
        return self.text


class Reader:
    def __init__(self, pages, encrypted=False):
        self.pages = [Page(text) for text in pages]
        self.is_encrypted = encrypted


class ConvertPdfTests(unittest.TestCase):
    def test_writes_page_markers_and_flags_empty_pages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.pdf"
            original.write_bytes(b"%PDF-invented")
            output = convert(original, root / "private", lambda _path: Reader(["Title\nMethods", ""]))
            text = (output.parent / "manuscript.txt").read_text(encoding="utf-8")
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(text, "[Page 1]\nTitle\nMethods\n[Page 2]\n")
            self.assertEqual(record["page_count"], 2)
            self.assertEqual(record["format"], "pdf")
            self.assertEqual(record["source_sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
            self.assertTrue(any("page(s): 2" in warning for warning in record["warnings"]))
            self.assertFalse((output.parent / "paper.pdf").exists())

    def test_rejects_scanned_and_encrypted_pdfs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.pdf"
            original.write_bytes(b"%PDF-invented")
            with self.assertRaisesRegex(ValueError, "require OCR"):
                convert(original, root / "private", lambda _path: Reader(["", ""]))
            with self.assertRaisesRegex(ValueError, "Encrypted"):
                convert(original, root / "private", lambda _path: Reader(["Text"], encrypted=True))
            self.assertFalse((root / "private").exists())

    def test_rejects_repository_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary) / "paper.pdf"
            original.write_bytes(b"%PDF-invented")
            repository = Path(__file__).resolve().parents[3]
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                convert(original, repository / "private", lambda _path: Reader(["Text"]))

    @unittest.skipUnless(importlib.util.find_spec("pypdf") and importlib.util.find_spec("reportlab"),
                         "Optional PDF packages are not installed")
    def test_extracts_real_generated_pdf(self):
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.pdf"
            document = canvas.Canvas(str(original))
            document.drawString(72, 720, "Invented manuscript title")
            document.save()
            output = convert(original, root / "private")
            extracted = (output.parent / "manuscript.txt").read_text(encoding="utf-8")
            self.assertIn("Invented manuscript title", extracted)
            self.assertIn("[Page 1]", extracted)


if __name__ == "__main__":
    unittest.main()
