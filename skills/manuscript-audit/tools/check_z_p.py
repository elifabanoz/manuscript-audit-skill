#!/usr/bin/env python3
"""Flag provisional inconsistencies in explicitly two-sided z and p reports."""

import argparse
import json
import math
import re
from decimal import Decimal
from pathlib import Path

from build_coverage import corpus_hash
from list_dois import read_corpus
from screenable_lines import screenable_lines


PAIR = re.compile(
    r"\b(?:two[- ]sided|two[- ]tailed)\s+z\s*=\s*"
    r"(?P<z>[+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*[,;]?\s*"
    r"p\s*(?P<relation>=|<|≤)\s*(?P<p>(?:\d+(?:\.\d+)?|\.\d+))(?![A-Za-z0-9.])",
    re.IGNORECASE,
)


def rounding_half_unit(value):
    digits = len(value.split(".", 1)[1]) if "." in value else 0
    return Decimal(1).scaleb(-digits) / 2


def evaluate(z_text, relation, p_text):
    z = Decimal(z_text)
    reported_p = Decimal(p_text)
    if not 0 <= reported_p <= 1:
        return {"status": "unresolved", "reason": "Reported p is outside [0, 1]"}
    z_half = rounding_half_unit(z_text)
    lower_z, upper_z = z - z_half, z + z_half
    minimum_abs_z = Decimal(0) if lower_z <= 0 <= upper_z else min(abs(lower_z), abs(upper_z))
    maximum_abs_z = max(abs(lower_z), abs(upper_z))
    expected_low = math.erfc(float(maximum_abs_z) / math.sqrt(2))
    expected_high = math.erfc(float(minimum_abs_z) / math.sqrt(2))
    expected_range = [expected_low, expected_high]
    if relation == "=":
        p_half = rounding_half_unit(p_text)
        reported_low = max(Decimal(0), reported_p - p_half)
        reported_high = min(Decimal(1), reported_p + p_half)
        incompatible = expected_high < float(reported_low) - 1e-12 or expected_low > float(reported_high) + 1e-12
        status = "needs_review" if incompatible else "compatible"
    elif expected_low > float(reported_p) + 1e-12:
        status = "needs_review"
    elif expected_high < float(reported_p) - 1e-12:
        status = "compatible"
    else:
        status = "unresolved"
    return {"status": status, "expected_p_range": expected_range}


def check(run):
    manifest, files = read_corpus(run)
    results = []
    for path, content in files:
        for line_number, line, context in screenable_lines(path, content):
            if re.search(r"\bone[- ](?:sided|tailed)\b", line, re.IGNORECASE):
                continue
            for match in PAIR.finditer(line):
                if re.search(r"\bnot\s+$", line[:match.start()], re.IGNORECASE):
                    continue
                result = (evaluate(match.group("z"), match.group("relation"), match.group("p"))
                          if context == "prose" else
                          {"status": "unresolved", "reason": (
                              "Opening Markdown metadata has no closing delimiter"
                              if context == "ambiguous_frontmatter" else
                              "Indented Markdown may be code or list prose")})
                results.append({
                    "path": path,
                    "line_id": f"L{line_number}",
                    "reported_z": match.group("z"),
                    "p_relation": match.group("relation"),
                    "reported_p": match.group("p"),
                    **result,
                })
    output = {
        "schema_version": 1,
        "corpus_sha256": corpus_hash(manifest),
        "method": "Two-sided normal-tail probability with rounding intervals for printed z and equality p values",
        "note": "Only explicit same-line two-sided z/p pairs are screened; closed Markdown metadata, fenced code, and BibTeX are excluded. Unclosed metadata and indented Markdown matches stay unresolved. Review every flag against the original manuscript and test assumptions",
        "checks": results,
    }
    output_path = run.resolve() / "z-checks.json"
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2)
        handle.write("\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        output = check(args.run)
        print(json.dumps({"checks": len(output["checks"]), "output": str(args.run / "z-checks.json")}))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
