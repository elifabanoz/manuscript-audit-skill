"""Parsing benchmark checks use invented documents in temporary private folders."""

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "manuscript-audit" / "tools"))
from convert_docx import convert  # noqa: E402
from convert_tex import convert as convert_tex  # noqa: E402

from score import measure, parse_markdown, score  # noqa: E402


WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def paragraph(text, heading=False):
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    return f"<w:p>{style}<w:r><w:t>{text}</w:t></w:r></w:p>"


def invented_docx(path):
    body = (paragraph("Introduction", True) + paragraph("Invented methods text.")
            + paragraph("References", True)
            + paragraph("Ada, A. (2024). Invented Alpha.")
            + paragraph("Ben, B. (2025). Invented Beta."))
    xml = f'<w:document xmlns:w="{WORD}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


class ParsingScoreTests(unittest.TestCase):
    def test_docx_conversion_and_gold_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)
            source = private / "paper.docx"
            invented_docx(source)
            extraction = convert(source, private / "conversions")
            record = json.loads(extraction.read_text(encoding="utf-8"))
            gold = {
                "schema_version": 1,
                "data_freeze": "2026-10-23",
                "cases": [{
                    "case_id": "invented-docx",
                    "format": "docx",
                    "source_file": str(source),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "extraction_record": str(extraction),
                    "extracted_sha256": record["markdown_sha256"],
                    "sections": ["Introduction", "References"],
                    "references": ["Ada, A. (2024). Invented Alpha.",
                                   "Ben, B. (2025). Invented Beta."],
                }],
            }
            gold_path = private / "gold.json"
            gold_path.write_text(json.dumps(gold), encoding="utf-8")
            output = score(gold_path, private / "result.json")
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["aggregate"]["sections"]["tp"], 2)
            self.assertEqual(result["aggregate"]["references"]["tp"], 2)
            self.assertEqual(result["aggregate"]["references"]["f1"], 1.0)
            self.assertEqual(result["cases"][0]["extracted_sha256"], record["markdown_sha256"])

            (extraction.parent / "manuscript.md").write_text("# References\nMissing\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "extracted Markdown differs"):
                score(gold_path, private / "tampered-result.json")

    @unittest.skipUnless(shutil.which("pandoc"), "Pandoc is required for LaTeX conversion")
    def test_latex_conversion_preserves_paragraph_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)
            source = private / "paper.tex"
            source.write_text(
                "\\section{Introduction}\nInvented methods text.\n\n"
                "\\section{References}\nAda, A. Invented Alpha.\n\n"
                "Ben, B. Invented Beta.\n", encoding="utf-8")
            extraction = convert_tex(source, private / "conversions")
            record = json.loads(extraction.read_text(encoding="utf-8"))
            gold = {"schema_version": 1, "data_freeze": "2026-10-23", "cases": [{
                "case_id": "invented-tex", "format": "tex", "source_file": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "extraction_record": str(extraction),
                "extracted_sha256": record["markdown_sha256"],
                "sections": ["Introduction", "References"],
                "references": ["Ada, A. Invented Alpha.", "Ben, B. Invented Beta."],
            }]}
            gold_path = private / "gold.json"
            gold_path.write_text(json.dumps(gold), encoding="utf-8")
            result = json.loads(score(gold_path, private / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["aggregate"]["sections"]["recall"], 1.0)
            self.assertEqual(result["aggregate"]["references"]["recall"], 1.0)

    def test_markdown_structure_excludes_frontmatter_and_code(self):
        text = ("---\ntitle: Hidden heading\n---\n# Introduction\n"
                "```markdown\n# Not a section\n```\n"
                "References\n----------\n"
                "- Ada, A. Invented Alpha.\n- Ben, B. Invented Beta.\n"
                "# Appendix\nNot a reference.\n")
        headings, references = parse_markdown(text)
        self.assertEqual(headings, ["Introduction", "References", "Appendix"])
        self.assertEqual(references, ["Ada, A. Invented Alpha.", "Ben, B. Invented Beta."])

    def test_missed_and_extra_predictions_are_visible(self):
        result = measure(["introduction", "references"], ["Introduction", "Methods"])
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 1, 1))
        self.assertEqual(result["precision"], 0.5)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(measure(["missing"], ["extra"])["f1"], 0.0)

    def test_gold_cannot_be_loaded_from_repository(self):
        repository_gold = ROOT / "bench" / "parsing" / "gold.private.json"
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "outside Git"):
                score(repository_gold, Path(temporary) / "result.json")


if __name__ == "__main__":
    unittest.main()
