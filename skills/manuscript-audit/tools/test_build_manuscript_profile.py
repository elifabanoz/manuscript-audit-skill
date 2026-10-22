"""Tests for conservative manuscript structure inventory."""

import unittest

from build_manuscript_profile import markdown_structure, plain_text_structure


class ManuscriptProfileTests(unittest.TestCase):
    def test_markdown_maps_physical_lines_and_ignores_fenced_markers(self):
        text = "\n".join([
            "---", "title: Example", "---", "# Summary", "Visible text.",
            "```md", "# Fake section", "![fake](plot.png)", "| --- | --- |", "```",
            "## Details", "![plot](plot.png)", "Table 1: Results", "# References", "A citation.",
        ]) + "\n"
        result = markdown_structure(text)
        self.assertEqual(result["status"], "atx_markdown_observed")
        self.assertEqual([(item["title"], item["heading_line_id"], item["end_line_id"])
                          for item in result["sections"]], [
                              ("Summary", "L4", "L13"),
                              ("Details", "L11", "L13"),
                              ("References", "L14", "L15"),
                          ])
        self.assertEqual(result["reference_section"],
                         {"status": "located", "heading_line_id": "L14", "end_line_id": "L15"})
        self.assertEqual(result["figure_marker_lines"], ["L12"])
        self.assertEqual(result["table_marker_lines"], ["L13"])

    def test_unclosed_frontmatter_does_not_invent_sections(self):
        result = markdown_structure("---\ntitle: Example\n# Text in broken metadata\n")
        self.assertEqual(result["status"], "structure_unresolved")
        self.assertEqual(result["sections"], [])
        self.assertIn("no closing delimiter", result["warnings"][0])

    def test_pdf_page_markers_do_not_become_sections_or_references(self):
        result = plain_text_structure("[Page 1]\nREFERENCES\n[Page 2]\nA citation.\n", {"format": "pdf"})
        self.assertEqual(result["status"], "structure_unresolved")
        self.assertEqual(result["sections"], [])
        self.assertEqual(result["reference_section"], {"status": "unresolved"})
        self.assertEqual(result["page_markers"], [{"page": 1, "line_id": "L1"},
                                                   {"page": 2, "line_id": "L3"}])


if __name__ == "__main__":
    unittest.main()
