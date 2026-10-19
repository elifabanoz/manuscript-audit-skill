#!/usr/bin/env python3
"""Download pinned public paper sources into a private directory."""

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path


MANIFEST = Path(__file__).with_name("public-papers.json")
DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
OUTPUT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\.md$")
MAX_BYTES = 5_000_000


def outside_git(path):
    resolved = path.resolve()
    for directory in (resolved, *resolved.parents):
        if (directory / ".git").exists():
            raise ValueError("Paper sources must stay outside a Git checkout")


def validate_paper(paper):
    keys = {"doi", "journal", "article_url", "article_license", "source_url", "source_ref", "source_sha256", "output_name"}
    if not isinstance(paper, dict) or set(paper) != keys:
        raise ValueError("Paper metadata has missing or unexpected fields")
    if not isinstance(paper["doi"], str) or not DOI.fullmatch(paper["doi"]):
        raise ValueError("Invalid DOI")
    if not isinstance(paper["source_sha256"], str) or not SHA256.fullmatch(paper["source_sha256"]):
        raise ValueError(f"{paper['doi']}: invalid SHA-256")
    if not isinstance(paper["output_name"], str) or not OUTPUT_NAME.fullmatch(paper["output_name"]):
        raise ValueError(f"{paper['doi']}: invalid output name")
    if not isinstance(paper["article_url"], str) or not isinstance(paper["source_url"], str) or not paper["article_url"].startswith("https://") or not paper["source_url"].startswith("https://raw.githubusercontent.com/"):
        raise ValueError(f"{paper['doi']}: source URLs must use the expected HTTPS hosts")
    if not all(isinstance(paper[key], str) and paper[key].strip() for key in ("journal", "article_license", "source_ref")):
        raise ValueError(f"{paper['doi']}: incomplete publication metadata")


def download(paper, directory):
    validate_paper(paper)
    target = directory / paper["output_name"]
    outside_git(target)
    if target.exists():
        if target.stat().st_size > MAX_BYTES:
            raise ValueError(f"{paper['doi']}: existing file exceeds the size limit")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != paper["source_sha256"]:
            raise ValueError(f"{paper['doi']}: existing file has a different SHA-256")
        return target, "verified existing"
    request = urllib.request.Request(paper["source_url"], headers={"User-Agent": "manuscript-audit-benchmark/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"{paper['doi']}: source exceeds the size limit")
    if hashlib.sha256(data).hexdigest() != paper["source_sha256"]:
        raise ValueError(f"{paper['doi']}: downloaded SHA-256 differs from the pinned value")
    directory.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as output:
        output.write(data)
    return target, "downloaded"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path, help="Private directory outside every Git checkout")
    parser.add_argument("--doi", help="Download only this DOI")
    args = parser.parse_args()
    try:
        if not args.output_directory.is_absolute():
            raise ValueError("Output directory must be an absolute path")
        outside_git(args.output_directory)
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if set(manifest) != {"schema_version", "papers"} or manifest["schema_version"] != 1 or not isinstance(manifest["papers"], list):
            raise ValueError("Invalid public paper manifest")
        papers = manifest["papers"]
        if args.doi:
            papers = [paper for paper in papers if paper.get("doi") == args.doi]
            if not papers:
                raise ValueError("DOI is absent from the public paper manifest")
        if len({paper["doi"] for paper in papers}) != len(papers) or len({paper["output_name"] for paper in papers}) != len(papers):
            raise ValueError("Duplicate DOI or output name")
        for paper in papers:
            target, status = download(paper, args.output_directory)
            print(f"{paper['doi']}: {status}: {target}")
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
