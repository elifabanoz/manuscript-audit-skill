"""The IEEE Access adapter accepts only its current, exact journal identity."""

import unittest

from publishers.ieee_access import GUIDELINE_URL, submission_guidelines


SOURCE = {"id": "https://openalex.org/S2485537415", "issn_l": "2169-3536",
          "host_organization": "https://openalex.org/P4310319808",
          "homepage_url": "http://ieeexplore.ieee.org/servlet/opac?punumber=6287639"}


class IEEEAccessTests(unittest.TestCase):
    def test_maps_exact_current_source(self):
        self.assertEqual(submission_guidelines(SOURCE, "2169-3536"), GUIDELINE_URL)

    def test_rejects_other_sources_and_homepages(self):
        for field, value in (
                ("id", "https://openalex.org/S1"),
                ("issn_l", "0000-0000"),
                ("host_organization", "https://openalex.org/P1"),
                ("homepage_url", "http://ieeexplore.ieee.org/servlet/opac?punumber=1"),
                ("homepage_url", "http://ieeexplore.ieee.org.evil.example/servlet/opac?punumber=6287639"),
                ("homepage_url", "http://ieeexplore.ieee.org/servlet/opac?punumber=6287639&next=evil"),
                ("homepage_url", "http://ieeexplore.ieee.org:8080/servlet/opac?punumber=6287639"),
                ("homepage_url", "http://user@ieeexplore.ieee.org/servlet/opac?punumber=6287639")):
            with self.subTest(field=field, value=value):
                self.assertIsNone(submission_guidelines({**SOURCE, field: value}, "2169-3536"))
        self.assertIsNone(submission_guidelines(SOURCE, "1234-567X"))


if __name__ == "__main__":
    unittest.main()
