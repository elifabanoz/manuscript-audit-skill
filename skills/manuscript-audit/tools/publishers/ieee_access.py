"""Resolve IEEE Access instructions from its current, ISSN-matched OpenAlex record."""

from urllib.parse import parse_qs, urlsplit


SOURCE_ID = "https://openalex.org/S2485537415"
PUBLISHER_ID = "https://openalex.org/P4310319808"
ISSN = "2169-3536"
GUIDELINE_URL = "https://ieeeaccess.ieee.org/authors/submission-guidelines/"


def submission_guidelines(source, issn):
    if (not isinstance(source, dict) or issn != ISSN or source.get("id") != SOURCE_ID
            or source.get("issn_l") != ISSN or source.get("host_organization") != PUBLISHER_ID):
        return None
    homepage = source.get("homepage_url")
    if not isinstance(homepage, str):
        return None
    try:
        parsed = urlsplit(homepage)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname != "ieeexplore.ieee.org"
                or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)
                or parsed.path != "/servlet/opac"
                or parse_qs(parsed.query, keep_blank_values=True) != {"punumber": ["6287639"]}):
            return None
    except ValueError:
        return None
    return GUIDELINE_URL
