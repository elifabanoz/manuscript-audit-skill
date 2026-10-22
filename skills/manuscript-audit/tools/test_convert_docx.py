"""DOCX conversion tests use invented OOXML packages only."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from convert_docx import convert
from prepare_run import prepare


NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path, body, styles=None):
    document = f'<w:document xmlns:w="{NAMESPACE}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        if styles is not None:
            archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{NAMESPACE}">{styles}</w:styles>')


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

    def test_prepared_run_binds_original_docx_and_extraction_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.docx"
            make_docx(original, '<w:p><w:r><w:t>Invented manuscript</w:t></w:r></w:p>')
            extraction = convert(original, root / "private")
            guideline = root / "guide.txt"
            guideline.write_text("Include a data statement.\n", encoding="utf-8")
            run = prepare(argparse.Namespace(
                manuscript=extraction.parent / "manuscript.md", extraction_record=extraction,
                guideline_text=guideline, guideline_url="https://example.org/authors",
                retrieved_at="2026-10-19T09:00:00Z", supplementary=[],
                output_root=root / "runs", host="manual", model=[]))
            manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["extraction"]["source_sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
            validator = Path(__file__).resolve().parents[1] / "validators" / "validate-run-manifest.cjs"
            self.assertEqual(subprocess.run(["node", str(validator), str(run)], capture_output=True).returncode, 0)
            (run / "extraction.json").write_text("{}", encoding="utf-8")
            self.assertEqual(subprocess.run(["node", str(validator), str(run)], capture_output=True).returncode, 1)

    def test_rejects_tracked_changes_instead_of_guessing_final_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.docx"
            make_docx(original, '<w:p><w:ins><w:r><w:t>New text</w:t></w:r></w:ins></w:p>')
            with self.assertRaisesRegex(ValueError, "unsupported revision"):
                convert(original, root / "private")
            self.assertFalse((root / "private").exists())

    def test_rejects_wrapped_body_text_instead_of_silently_omitting_it(self):
        wrappers = ["sdt", "customXml"]
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original = root / "paper.docx"
                if wrapper == "sdt":
                    wrapped = '<w:sdt><w:sdtContent><w:p><w:r><w:t>Omitted text</w:t></w:r></w:p></w:sdtContent></w:sdt>'
                else:
                    wrapped = '<w:customXml><w:p><w:r><w:t>Omitted text</w:t></w:r></w:p></w:customXml>'
                make_docx(original, '<w:p><w:r><w:t>Visible text</w:t></w:r></w:p>' + wrapped)
                with self.assertRaisesRegex(ValueError, wrapper):
                    convert(original, root / "private")
                self.assertFalse((root / "private").exists())

    def test_rejects_nested_tables_instead_of_silently_omitting_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.docx"
            make_docx(original,
                      '<w:p><w:r><w:t>Visible text</w:t></w:r></w:p>'
                      '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Outer cell</w:t></w:r></w:p>'
                      '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Nested cell</w:t></w:r></w:p>'
                      '</w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl>')
            with self.assertRaisesRegex(ValueError, "nested or wrapped table"):
                convert(original, root / "private")
            self.assertFalse((root / "private").exists())

    def test_rejects_hidden_or_low_visibility_text_before_writing(self):
        cases = [
            ('<w:rPr><w:vanish/></w:rPr>', 'vanish'),
            ('<w:rPr><w:webHidden/></w:rPr>', 'webHidden'),
            ('<w:rPr><w:sz w:val="0"/></w:rPr>', 'zero font size'),
            ('<w:rPr><w:color w:val="FFFFFF"/></w:rPr>', 'white font color'),
        ]
        for properties, marker in cases:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original = root / "paper.docx"
                make_docx(original, f'<w:p><w:r>{properties}<w:t>Hidden instruction</w:t></w:r></w:p>')
                with self.assertRaisesRegex(ValueError, marker):
                    convert(original, root / "private")
                self.assertFalse((root / "private").exists())

    def test_rejects_hidden_text_from_an_applied_style(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.docx"
            styles = ('<w:style w:styleId="Base"><w:rPr><w:vanish/></w:rPr></w:style>'
                      '<w:style w:styleId="Concealed"><w:basedOn w:val="Base"/></w:style>')
            make_docx(original,
                      '<w:p><w:pPr><w:pStyle w:val="Concealed"/></w:pPr>'
                      '<w:r><w:t>Hidden instruction</w:t></w:r></w:p>', styles)
            with self.assertRaisesRegex(ValueError, "vanish"):
                convert(original, root / "private")
            self.assertFalse((root / "private").exists())

    def test_rejects_hidden_text_from_default_and_character_styles(self):
        cases = [
            ('<w:docDefaults><w:rPrDefault><w:rPr><w:vanish/></w:rPr></w:rPrDefault></w:docDefaults>',
             '<w:p><w:r><w:t>Hidden text</w:t></w:r></w:p>'),
            ('<w:style w:styleId="Concealed"><w:rPr><w:vanish/></w:rPr></w:style>',
             '<w:p><w:r><w:rPr><w:rStyle w:val="Concealed"/></w:rPr><w:t>Hidden text</w:t></w:r></w:p>'),
        ]
        for styles, body in cases:
            with self.subTest(styles=styles), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original = root / "paper.docx"
                make_docx(original, body, styles)
                with self.assertRaisesRegex(ValueError, "vanish"):
                    convert(original, root / "private")

    def test_explicitly_disabled_hidden_property_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.docx"
            make_docx(original,
                      '<w:p><w:r><w:rPr><w:vanish w:val="0"/></w:rPr>'
                      '<w:t>Visible text</w:t></w:r></w:p>')
            output = convert(original, root / "private")
            self.assertEqual((output.parent / "manuscript.md").read_text(encoding="utf-8"), "Visible text\n")

    def test_rejects_output_inside_git_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary) / "paper.docx"
            make_docx(original, '<w:p><w:r><w:t>Text</w:t></w:r></w:p>')
            repository = Path(__file__).resolve().parents[3]
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                convert(original, repository / "private")


if __name__ == "__main__":
    unittest.main()
