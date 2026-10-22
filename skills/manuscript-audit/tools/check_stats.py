#!/usr/bin/env python3
"""Screen explicitly reported t, F, chi-square, and Pearson r/p pairs."""

import argparse
import json
import math
import re
from decimal import Decimal
from pathlib import Path

from build_coverage import corpus_hash
from list_dois import read_corpus
from screenable_lines import screenable_lines


NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
ENDING = rf"\s*[,;]?\s*p\s*(?P<relation>=|<|≤)\s*(?P<p>{NUMBER})(?![A-Za-z0-9.])"
PATTERNS = {
    "t": re.compile(rf"\b(?:two[- ]sided|two[- ]tailed)\s+t\(\s*(?P<df1>\d+)\s*\)\s*"
                    rf"=\s*(?P<value>{NUMBER}){ENDING}", re.IGNORECASE),
    "F": re.compile(rf"\bF\(\s*(?P<df1>\d+)\s*,\s*(?P<df2>\d+)\s*\)\s*"
                    rf"=\s*(?P<value>{NUMBER}){ENDING}"),
    "chi_square": re.compile(rf"(?:χ²|χ\^2|χ2|chi[- ]?square(?:d)?)\(\s*(?P<df1>\d+)\s*\)\s*"
                             rf"=\s*(?P<value>{NUMBER}){ENDING}", re.IGNORECASE),
    "pearson_r": re.compile(rf"\b(?:two[- ]sided|two[- ]tailed)\s+Pearson\s+r\(\s*(?P<df1>\d+)\s*\)\s*"
                            rf"=\s*(?P<value>{NUMBER}){ENDING}", re.IGNORECASE),
}
CONTEXT_EXCLUSION = re.compile(r"\b(?:one[- ]sided|one[- ]tailed|adjusted|corrected|bonferroni|post[- ]hoc)\b",
                               re.IGNORECASE)
EPSILON = 1e-12


def half_unit(printed):
    return Decimal(1).scaleb(Decimal(printed).as_tuple().exponent) / 2


def probability_range(kind, value_text, dfs, stats):
    value = Decimal(value_text)
    if not all(df > 0 for df in dfs) or (kind in {"F", "chi_square"} and value < 0):
        return None
    if kind == "pearson_r" and not -1 <= value <= 1:
        return None
    lower = value - half_unit(value_text)
    upper = value + half_unit(value_text)
    if kind in {"t", "pearson_r"}:
        minimum = Decimal(0) if lower <= 0 <= upper else min(abs(lower), abs(upper))
        maximum = max(abs(lower), abs(upper))
        if kind == "pearson_r":
            minimum, maximum = min(minimum, Decimal(1)), min(maximum, Decimal(1))
    else:
        minimum, maximum = max(lower, Decimal(0)), max(upper, Decimal(0))
    try:
        low, high = float(minimum), float(maximum)
    except OverflowError:
        return None
    if kind == "t":
        bounds = (2 * stats.t.sf(high, dfs[0]), 2 * stats.t.sf(low, dfs[0]))
    elif kind == "F":
        bounds = (stats.f.sf(high, *dfs), stats.f.sf(low, *dfs))
    elif kind == "chi_square":
        bounds = (stats.chi2.sf(high, dfs[0]), stats.chi2.sf(low, dfs[0]))
    else:
        distribution = stats.beta(dfs[0] / 2, dfs[0] / 2, loc=-1, scale=2)
        bounds = (2 * distribution.cdf(-high), 2 * distribution.cdf(-low))
    if not all(math.isfinite(bound) and 0 <= bound <= 1 for bound in bounds):
        return None
    return [float(bound) for bound in bounds]


def evaluate(kind, value_text, dfs, relation, p_text, stats):
    reported = Decimal(p_text)
    if not 0 <= reported <= 1:
        return {"status": "unresolved", "reason": "Reported p is outside [0, 1]"}
    expected = probability_range(kind, value_text, dfs, stats)
    if expected is None:
        return {"status": "unresolved", "reason": "Statistic or degrees of freedom are invalid"}
    expected_low, expected_high = expected
    if relation == "=":
        radius = half_unit(p_text)
        reported_low = max(Decimal(0), reported - radius)
        reported_high = min(Decimal(1), reported + radius)
        inconsistent = (expected_high < float(reported_low) - EPSILON
                        or expected_low > float(reported_high) + EPSILON)
        status = "needs_review" if inconsistent else "compatible"
    else:
        reported_low, reported_high = Decimal(0), reported
        if expected_low > float(reported_high) + EPSILON:
            status = "needs_review"
        elif expected_high < float(reported_high) - EPSILON:
            status = "compatible"
        else:
            status = "unresolved"
    result = {"status": status, "expected_p_range": expected}
    if status == "needs_review":
        threshold = Decimal("0.05")
        opposite = ((reported_high < threshold and expected_low > float(threshold) + EPSILON)
                    or (reported_low > threshold and expected_high < float(threshold) - EPSILON))
        result["severity_proposed"] = "major" if opposite else "minor"
    return result


def check(run):
    try:
        from scipy import __version__ as scipy_version, stats
    except ImportError:
        raise ValueError("Install the optional statistics dependency from requirements-stats.txt") from None
    manifest, files = read_corpus(run)
    checks = []
    for path, content in files:
        for line_number, line, context in screenable_lines(path, content):
            if CONTEXT_EXCLUSION.search(line):
                continue
            for kind, pattern in PATTERNS.items():
                for match in pattern.finditer(line):
                    if re.search(r"\bnot\s+$", line[:match.start()], re.IGNORECASE):
                        continue
                    dfs = [int(match.group("df1"))]
                    if kind == "F":
                        dfs.append(int(match.group("df2")))
                    decision = (evaluate(kind, match.group("value"), dfs,
                                         match.group("relation"), match.group("p"), stats)
                                if context == "prose" else
                                {"status": "unresolved", "reason": (
                                    "Opening Markdown metadata has no closing delimiter"
                                    if context == "ambiguous_frontmatter" else
                                    "Indented Markdown may be code or list prose")})
                    checks.append({"path": path, "line_id": f"L{line_number}",
                                   "statistic": kind, "reported_value": match.group("value"),
                                   "degrees_of_freedom": dfs,
                                   "p_relation": match.group("relation"),
                                   "reported_p": match.group("p"),
                                   **decision})
    output = {"schema_version": 1, "corpus_sha256": corpus_hash(manifest),
              "scipy_version": scipy_version,
              "method": "Distribution tails over the rounding interval of each printed statistic and p value",
              "note": "Only explicit same-line patterns are screened; closed Markdown metadata, fenced code, and BibTeX are excluded. Unclosed metadata and indented Markdown matches stay unresolved. Every flag requires review of the original test, tail, corrections, and assumptions",
              "checks": checks}
    output_path = run.resolve() / "statistical-checks.json"
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
        print(json.dumps({"checks": len(output["checks"]),
                          "output": str(args.run / "statistical-checks.json")}))
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
