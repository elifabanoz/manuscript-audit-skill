"""LaTeX conversion tests use invented local manuscripts only."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from convert_tex import convert
from prepare_run import prepare


PAPER = r"""\documentclass{article}
\title{Invented Study}
\author{Researcher}
\begin{document}
\maketitle
\begin{abstract}
Short abstract.
\end{abstract}
\section{Methods}
Invented method with \cite{sample}.
\end{document}
"""


@unittest.skipUnless(shutil.which("pandoc"), "Pandoc is not installed")
class ConvertTexTests(unittest.TestCase):
    def test_converts_title_abstract_heading_and_citation_privately(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.tex"
            original.write_text(PAPER, encoding="utf-8")
            output = convert(original, root / "private")
            markdown = (output.parent / "manuscript.md").read_text(encoding="utf-8")
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("Invented Study", markdown)
            self.assertIn("Short abstract.", markdown)
            self.assertIn("# Methods", markdown)
            self.assertIn("[@sample]", markdown)
            self.assertEqual(record["source_sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
            self.assertEqual(record["markdown_sha256"], hashlib.sha256(markdown.encode()).hexdigest())
            self.assertFalse((output.parent / "paper.tex").exists())

    def test_prepared_run_binds_original_tex_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.tex"
            original.write_text(PAPER, encoding="utf-8")
            extraction = convert(original, root / "private")
            guideline = root / "guide.txt"
            guideline.write_text("Include a data statement.\n", encoding="utf-8")
            run = prepare(argparse.Namespace(
                manuscript=extraction.parent / "manuscript.md", extraction_record=extraction,
                guideline_text=guideline, guideline_url="https://example.org/authors",
                retrieved_at="2026-10-19T09:00:00Z", supplementary=[],
                output_root=root / "runs", host="manual", model=[]))
            manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["extraction"]["format"], "tex")
            self.assertEqual(manifest["extraction"]["source_sha256"], hashlib.sha256(original.read_bytes()).hexdigest())
            validator = Path(__file__).resolve().parents[1] / "validators" / "validate-run-manifest.cjs"
            self.assertEqual(subprocess.run(["node", str(validator), str(run)], capture_output=True).returncode, 0)

    def test_rejects_external_source_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "paper.tex"
            original.write_text(PAPER.replace("\\section{Methods}", "\\input{methods}\n\\section{Methods}"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "another source file"):
                convert(original, root / "private")
            self.assertFalse((root / "private").exists())

    def test_rejects_output_inside_git_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary) / "paper.tex"
            original.write_text(PAPER, encoding="utf-8")
            repository = Path(__file__).resolve().parents[3]
            with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
                convert(original, repository / "private")


if __name__ == "__main__":
    unittest.main()
