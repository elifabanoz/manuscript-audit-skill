"""DOCX conversion tests use invented OOXML packages only."""

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from convert_docx import convert


NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path, body):
    document = f'<w:document xmlns:w="{NAMESPACE}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


class ConvertDocxTests(unittest.TestCase):
    def test_extracts_headings_paragraphs_and_tables_privately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.docx"
            make_docx(original,
                      '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Methods</w:t></w:r></w:p>'
                      '<w:p><w:r><w:t>Invented study</w:t></w:r></w:p>'
                      '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Group</w:t></w:r></w:p></w:tc>'
                      '<w:tc><w:p><w:r><w:t>Count</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
            output = convert(original, root / "private")
            markdown = (output.parent / "manuscript.md").read_text(encoding="utf-8")
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(markdown, "# Methods\nInvented study\nGroup | Count\n")
            self.assertEqual(record["source_sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
            self.assertEqual(record["markdown_sha256"], hashlib.sha256(markdown.encode()).hexdigest())
            self.assertTrue(any("table" in warning for warning in record["warnings"]))
            self.assertFalse((output.parent / "paper.docx").exists())

    def test_rejects_tracked_changes_instead_of_guessing_final_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.docx"
            make_docx(original, '<w:p><w:ins><w:r><w:t>New text</w:t></w:r></w:ins></w:p>')
            with self.assertRaisesRegex(ValueError, "unsupported revision"):
                convert(original, root / "private")
            self.assertFalse((root / "private").exists())

    def test_rejects_output_inside_git_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary) / "paper.docx"
            make_docx(original, '<w:p><w:r><w:t>Text</w:t></w:r></w:p>')
            repository = Path(__file__).resolve().parents[3]
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                convert(original, repository / "private")


if __name__ == "__main__":
    unittest.main()
