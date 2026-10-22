#!/usr/bin/env python3
"""Shortlist journals from an explicit topic query using OpenAlex metadata."""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from fetch_guidelines import https_url
from http_client import read_response
from prepare_run import outside_git


API_URL = "https://api.openalex.org/works"
SOURCE_URL = "https://api.openalex.org/sources"
DOAJ_URL = "https://doaj.org/api/search/journals/"
CROSSREF_JOURNAL_URL = "https://api.crossref.org/journals/"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
ISSN = re.compile(r"^\d{4}-\d{3}[\dX]$")
SOURCE_ID = re.compile(r"^https://openalex\.org/S\d+$")


def shortlist(payload, limit):
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("OpenAlex returned an unexpected response")
    counts = defaultdict(set)
    names = {}
    issns = {}
    for work in payload["results"]:
        if not isinstance(work, dict) or not isinstance(work.get("id"), str):
            continue
        location = work.get("primary_location")
        source = location.get("source") if isinstance(location, dict) else None
        if not isinstance(source, dict) or source.get("type") != "journal":
            continue
        source_id = source.get("id")
        name = source.get("display_name")
        issn_l = source.get("issn_l")
        if not (isinstance(source_id, str) and source_id.startswith("https://openalex.org/S")
                and isinstance(name, str) and name.strip()
                and isinstance(issn_l, str) and ISSN.fullmatch(issn_l)):
            continue
        counts[source_id].add(work["id"])
        names[source_id] = name.strip()
        issns[source_id] = issn_l
    candidates = [
        {"source_id": source_id, "display_name": names[source_id], "issn_l": issns[source_id],
         "matching_works": len(work_ids), "work_ids": sorted(work_ids)}
        for source_id, work_ids in counts.items()
    ]
    candidates.sort(key=lambda item: (-item["matching_works"], item["display_name"].casefold(), item["source_id"]))
    return candidates[:limit]


def fetch_json(url, opener, provider="OpenAlex"):
    request = Request(url, headers={"User-Agent": "manuscript-audit-skill/0.1", "Accept": "application/json"})
    api_key = os.environ.get("OPENALEX_API_KEY") if provider == "OpenAlex" else None
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        data = read_response(request, opener, timeout=20, max_bytes=MAX_RESPONSE_BYTES).body
    except HTTPError as error:
        raise ValueError(f"{provider} request failed with HTTP {error.code}") from None
    except URLError:
        raise ValueError(f"Could not reach {provider}") from None
    try:
        payload = json.loads(data)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError(f"{provider} returned invalid JSON") from None
    return payload, hashlib.sha256(data).hexdigest()


def work_filters(field_id, published_after):
    filters = ["type:article"]
    if field_id is not None:
        filters.append(f"primary_topic.field.id:{field_id}")
    if published_after is not None:
        filters.append(f"from_publication_date:{published_after}")
    return ",".join(filters)


def fetch_works(query, per_page, opener=urlopen, field_id=None, published_after=None):
    params = urlencode({"search.title_abstract_keywords": query, "filter": work_filters(field_id, published_after),
                        "per_page": per_page, "select": "id,primary_location"})
    return fetch_json(f"{API_URL}?{params}", opener)


def grouped_shortlist(query, limit, opener, field_id, published_after):
    params = urlencode({"search.title_abstract_keywords": query,
                        "filter": work_filters(field_id, published_after) + ",primary_location.source.type:journal",
                        "group_by": "primary_location.source.id", "per_page": 100})
    payload, response_hash = fetch_json(f"{API_URL}?{params}", opener)
    groups = payload.get("group_by") if isinstance(payload, dict) else None
    if not isinstance(groups, list):
        raise ValueError("OpenAlex returned an unexpected grouped response")
    ranked = []
    for group in groups:
        if (isinstance(group, dict) and isinstance(group.get("key"), str)
                and SOURCE_ID.fullmatch(group["key"]) and type(group.get("count")) is int
                and group["count"] > 0):
            ranked.append(group)
    ranked.sort(key=lambda item: (-item["count"], item["key"]))
    metadata = payload.get("meta")
    matching_works = metadata.get("count") if isinstance(metadata, dict) else None
    if not ranked:
        return [], response_hash, len(groups), matching_works
    source_ids = [group["key"].rsplit("/", 1)[1] for group in ranked]
    source_params = urlencode({"filter": "ids.openalex:" + "|".join(source_ids),
                               "select": "id,display_name,type,issn_l,issn,works_count,homepage_url",
                               "per_page": 100})
    sources_payload, _ = fetch_json(f"{SOURCE_URL}?{source_params}", opener)
    sources = sources_payload.get("results") if isinstance(sources_payload, dict) else None
    if not isinstance(sources, list):
        raise ValueError("OpenAlex returned an unexpected source response")
    source_by_id = {source.get("id"): source for source in sources if isinstance(source, dict)}
    candidates = []
    for group in ranked:
        source_id = group["key"]
        source = source_by_id.get(source_id)
        if not isinstance(source, dict) or source.get("id") != source_id or source.get("type") != "journal":
            continue
        name = source.get("display_name")
        issn_l = source.get("issn_l")
        total = source.get("works_count")
        if not (isinstance(name, str) and name.strip() and isinstance(issn_l, str)
                and ISSN.fullmatch(issn_l) and type(total) is int and total > 0):
            continue
        issns = {issn_l}
        if isinstance(source.get("issn"), list):
            issns.update(value for value in source["issn"] if isinstance(value, str) and ISSN.fullmatch(value))
        candidates.append({"source_id": source_id, "display_name": name.strip(), "issn_l": issn_l,
                           "issns": sorted(issns),
                           "homepage_url": source.get("homepage_url") if isinstance(source.get("homepage_url"), str) else None,
                           "matching_works": group["count"], "source_works_count": total,
                           "topic_share": round(group["count"] / total, 8), "evidence": ["openalex_grouped_works"]})
    candidates.sort(key=lambda item: (-(item["matching_works"] / item["source_works_count"]),
                                      -item["matching_works"],
                                      item["display_name"].casefold(), item["source_id"]))
    return candidates[:limit], response_hash, len(groups), matching_works


def safe_metadata_url(value):
    if not isinstance(value, str):
        return None
    try:
        return https_url(value)
    except ValueError:
        return None


def read_web_candidates(path):
    path = path.expanduser().resolve()
    outside_git(path)
    raw = path.read_bytes()
    if len(raw) > 32_768:
        raise ValueError("Web candidate input exceeds 32 KB")
    try:
        record = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Web candidate input is not valid JSON") from None
    entries = record.get("candidates") if isinstance(record, dict) and record.get("schema_version") == 1 else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError("Web candidate input needs 1 to 20 ISSN and URL pairs")
    seen = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"issn", "result_url"}:
            raise ValueError("Each web candidate needs only issn and result_url")
        issn, url = item["issn"], item["result_url"]
        if not isinstance(issn, str) or not ISSN.fullmatch(issn) or issn in seen:
            raise ValueError("Web candidate ISSNs must be unique and correctly shaped")
        if not isinstance(url, str) or len(url) > 2048 or any(value in url for value in "\r\n\x00"):
            raise ValueError("Web candidate result_url must be a public HTTPS URL")
        try:
            https_url(url)
            if urlsplit(url).port not in (None, 443):
                raise ValueError("nonstandard port")
        except ValueError:
            raise ValueError("Web candidate result_url must be a public HTTPS URL") from None
        seen.add(issn)
    return entries, hashlib.sha256(raw).hexdigest()


def registry_candidate(issn, result_url, opener):
    attempts = []

    def query(provider, url):
        try:
            payload, digest = fetch_json(url, opener, provider)
            return payload, digest
        except (OSError, ValueError) as error:
            attempts.append({"provider": provider, "status": "unavailable", "reason": str(error)})
            return None, None

    params = urlencode({"filter": f"issn:{issn}",
                        "select": "id,display_name,type,issn_l,issn,works_count,homepage_url",
                        "per_page": 5})
    payload, digest = query("OpenAlex", f"{SOURCE_URL}?{params}")
    results = payload.get("results") if isinstance(payload, dict) else None
    matches = [source for source in results if isinstance(source, dict)
               and source.get("type") == "journal" and isinstance(source.get("id"), str)
               and SOURCE_ID.fullmatch(source["id"])
               and isinstance(source.get("display_name"), str) and source["display_name"].strip()
               and issn in ([source.get("issn_l")] +
                            (source.get("issn") if isinstance(source.get("issn"), list) else []))] if isinstance(results, list) else []
    if digest is not None:
        attempts.append({"provider": "OpenAlex", "status": "matched" if len(matches) == 1 else "no_unique_match",
                         "response_sha256": digest})
    if len(matches) == 1:
        source = matches[0]
        issn_l = source.get("issn_l") if isinstance(source.get("issn_l"), str) and ISSN.fullmatch(source["issn_l"]) else issn
        issns = sorted({issn_l, issn, *(value for value in source.get("issn", [])
                                        if isinstance(value, str) and ISSN.fullmatch(value))}) if isinstance(source.get("issn"), list) else sorted({issn_l, issn})
        total = source.get("works_count")
        return {"source_id": source["id"], "display_name": source["display_name"].strip(),
                "issn_l": issn_l, "issns": issns,
                "homepage_url": safe_metadata_url(source.get("homepage_url")),
                "source_works_count": total if type(total) is int and total > 0 else None,
                "registry_provider": "OpenAlex", "registry_response_sha256": digest,
                "web_input_issn": issn,
                "web_result_url_unverified": result_url}, attempts

    query_text = quote(f"issn:{issn}", safe=":")
    payload, digest = query("DOAJ", f"{DOAJ_URL}{query_text}?page=1&pageSize=5")
    results = payload.get("results") if isinstance(payload, dict) else None
    matches = [item for item in results if isinstance(item, dict)
               and isinstance(item.get("id"), str) and item["id"]
               and isinstance(item.get("bibjson"), dict)
               and isinstance(item["bibjson"].get("title"), str) and item["bibjson"]["title"].strip()
               and issn in (item["bibjson"].get("eissn"), item["bibjson"].get("pissn"))] if isinstance(results, list) else []
    if digest is not None:
        attempts.append({"provider": "DOAJ", "status": "matched" if len(matches) == 1 else "no_unique_match",
                         "response_sha256": digest})
    if len(matches) == 1:
        item = matches[0]
        details = item["bibjson"]
        links = details.get("ref") if isinstance(details.get("ref"), dict) else {}
        return {"source_id": None, "doaj_id": item["id"], "display_name": details["title"].strip(),
                "issn_l": None, "issns": sorted({value for value in
                    (details.get("eissn"), details.get("pissn"))
                    if isinstance(value, str) and ISSN.fullmatch(value)}),
                "homepage_url": safe_metadata_url(links.get("journal")),
                "author_instructions_url": safe_metadata_url(links.get("author_instructions")),
                "source_works_count": None, "registry_provider": "DOAJ",
                "registry_response_sha256": digest,
                "web_input_issn": issn,
                "web_result_url_unverified": result_url}, attempts

    payload, digest = query("Crossref", f"{CROSSREF_JOURNAL_URL}{issn}")
    message = payload.get("message") if isinstance(payload, dict) else None
    valid = (isinstance(message, dict) and isinstance(message.get("title"), str)
             and message["title"].strip() and isinstance(message.get("ISSN"), list)
             and issn in message["ISSN"])
    if digest is not None:
        attempts.append({"provider": "Crossref", "status": "matched" if valid else "no_match",
                         "response_sha256": digest})
    if valid:
        return {"source_id": None, "display_name": message["title"].strip(),
                "issn_l": issn, "issns": sorted({value for value in message["ISSN"]
                                                  if isinstance(value, str) and ISSN.fullmatch(value)}),
                "homepage_url": None, "source_works_count": None,
                "registry_provider": "Crossref", "registry_response_sha256": digest,
                "web_input_issn": issn,
                "web_result_url_unverified": result_url}, attempts
    return None, attempts


def verify_web_candidates(entries, opener):
    verified = []
    rejected = []
    checks = []
    for item in entries:
        candidate, attempts = registry_candidate(item["issn"], item["result_url"], opener)
        checks.append({"issn": item["issn"], "registry_checks": attempts})
        if candidate is None:
            rejected.append({"issn": item["issn"], "registry_checks": attempts})
        else:
            verified.append(candidate)
    return verified, rejected, checks


def fetch_doaj_subject(subject, opener=urlopen):
    query = quote(f"index.classification:{subject}", safe=":")
    payload, response_hash = fetch_json(f"{DOAJ_URL}{query}?page=1&pageSize=50", opener, "DOAJ")
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or type(payload.get("total")) is not int:
        raise ValueError("DOAJ returned an unexpected journal response")
    candidates = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("bibjson"), dict):
            continue
        details = result["bibjson"]
        name = details.get("title")
        record_id = result.get("id")
        issns = sorted({value for value in (details.get("pissn"), details.get("eissn"))
                        if isinstance(value, str) and ISSN.fullmatch(value)})
        if not (isinstance(record_id, str) and record_id and isinstance(name, str)
                and name.strip() and issns):
            continue
        links = details.get("ref") if isinstance(details.get("ref"), dict) else {}
        candidates.append({"doaj_id": record_id, "display_name": name.strip(), "issns": issns,
                           "homepage_url": safe_metadata_url(links.get("journal")),
                           "author_instructions_url": safe_metadata_url(links.get("author_instructions"))})
    return candidates, response_hash, payload["total"], len(results)


def merge_doaj(openalex_candidates, doaj_candidates, limit):
    by_issn = {}
    ambiguous_issns = set()
    for candidate in openalex_candidates:
        for issn in candidate["issns"]:
            if issn in by_issn and by_issn[issn] is not candidate:
                ambiguous_issns.add(issn)
            else:
                by_issn[issn] = candidate
    for issn in ambiguous_issns:
        del by_issn[issn]
    supplemental = []
    seen_doaj = set()
    seen_supplemental_issns = set()
    for record in doaj_candidates:
        if record["doaj_id"] in seen_doaj:
            continue
        seen_doaj.add(record["doaj_id"])
        match = next((by_issn[issn] for issn in record["issns"] if issn in by_issn), None)
        if match is not None:
            if "doaj_subject" not in match["evidence"]:
                match["evidence"].append("doaj_subject")
            if "doaj_id" not in match:
                match["doaj_id"] = record["doaj_id"]
                match["author_instructions_url"] = record["author_instructions_url"]
        else:
            if seen_supplemental_issns.intersection(record["issns"]):
                continue
            seen_supplemental_issns.update(record["issns"])
            supplemental.append({"source_id": None, "doaj_id": record["doaj_id"],
                                 "display_name": record["display_name"], "issn_l": None,
                                 "issns": record["issns"], "homepage_url": record["homepage_url"],
                                 "author_instructions_url": record["author_instructions_url"],
                                 "matching_works": None, "source_works_count": None,
                                 "topic_share": None, "evidence": ["doaj_subject"]})
    openalex_candidates.sort(key=lambda item: (-len(item["evidence"]),
                                               -(item["matching_works"] / item["source_works_count"]),
                                               -item["matching_works"], item["display_name"].casefold()))
    supplemental.sort(key=lambda item: (item["display_name"].casefold(), item["doaj_id"]))
    reserve = min(limit // 5, len(supplemental))
    primary = openalex_candidates[:limit - reserve]
    extra = supplemental[:limit - len(primary)]
    if len(primary) + len(extra) < limit:
        primary += openalex_candidates[len(primary):limit - len(extra)]
    combined = primary + extra
    for candidate in combined:
        candidate["evidence_count"] = len(candidate["evidence"])
    return combined


def read_citation_record(path):
    path = path.expanduser().resolve()
    outside_git(path)
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("Citation record exceeds 1 MB")
    try:
        record = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Citation record is not valid JSON") from None
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("provider") != "OpenAlex" or not isinstance(record.get("candidates"), list)
            or not isinstance(record.get("reference_input_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["reference_input_sha256"])):
        raise ValueError("Citation record has an unexpected format")
    cleaned = []
    seen_ids = set()
    for item in record["candidates"]:
        if not isinstance(item, dict):
            raise ValueError("Citation record has an invalid candidate")
        source_id = item.get("source_id")
        issn_l = item.get("issn_l")
        issns = item.get("issns")
        counts = (item.get("reference_works"), item.get("citing_works"))
        total = item.get("source_works_count")
        evidence = item.get("evidence")
        if (not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id)
                or source_id in seen_ids or not isinstance(issn_l, str) or not ISSN.fullmatch(issn_l)
                or not isinstance(issns, list) or issn_l not in issns
                or any(not isinstance(value, str) or not ISSN.fullmatch(value) for value in issns)
                or any(type(value) is not int or value < 0 for value in counts)
                or type(total) is not int or total <= 0
                or not isinstance(evidence, list)
                or any(value not in {"reference_venues", "citing_venues"} for value in evidence)
                or ("reference_venues" in evidence) != bool(counts[0])
                or ("citing_venues" in evidence) != bool(counts[1])):
            raise ValueError("Citation record has an invalid candidate")
        seen_ids.add(source_id)
        cleaned.append({"source_id": source_id, "issn_l": issn_l, "issns": issns,
                        "display_name": item.get("display_name") if isinstance(item.get("display_name"), str) else source_id,
                        "homepage_url": safe_metadata_url(item.get("homepage_url")),
                        "reference_works": counts[0], "citing_works": counts[1], "source_works_count": total,
                        "citing_share": round(counts[1] / total, 8),
                        "citation_signal_details": [name for name in ("reference_venues", "citing_venues")
                                                    if name in evidence]})
    return cleaned, hashlib.sha256(raw).hexdigest(), record.get("reference_input_sha256")


def merge_citation(candidates, citation_candidates, limit):
    by_source = {item["source_id"]: item for item in candidates if item.get("source_id")}
    by_issn = {}
    ambiguous = set()
    for item in candidates:
        for issn in item.get("issns", []):
            if issn in by_issn and by_issn[issn] is not item:
                ambiguous.add(issn)
            else:
                by_issn[issn] = item
    for issn in ambiguous:
        del by_issn[issn]
    supplemental = []
    for item in citation_candidates:
        match = by_source.get(item["source_id"])
        if match is None:
            match = next((by_issn[issn] for issn in item["issns"] if issn in by_issn), None)
        if match is not None:
            if "citation_graph" not in match["evidence"]:
                match["evidence"].append("citation_graph")
            match["reference_works"] = item["reference_works"]
            match["citing_works"] = item["citing_works"]
            match["citing_share"] = item["citing_share"]
            match["citation_signal_details"] = item["citation_signal_details"]
        else:
            supplemental.append({**item, "matching_works": None,
                                 "topic_share": None, "evidence": ["citation_graph"]})
    def ranking(item):
        return (-len(item["evidence"]), -(item.get("topic_share") or 0),
                -(item.get("reference_works") or 0), -(item.get("citing_works") or 0),
                -(item.get("matching_works") or 0), item["display_name"].casefold())
    candidates.sort(key=ranking)
    supplemental.sort(key=ranking)
    doaj_only = [item for item in candidates if item["evidence"] == ["doaj_subject"]]
    primary = [item for item in candidates if item["evidence"] != ["doaj_subject"]]
    doaj_reserve = min(limit // 5, len(doaj_only))
    citation_reserve = min(limit // 5, len(supplemental))
    selected = primary[:limit - doaj_reserve - citation_reserve]
    selected += doaj_only[:doaj_reserve]
    selected += supplemental[:citation_reserve]
    selected_ids = {id(item) for item in selected}
    for item in sorted(candidates + supplemental, key=ranking):
        if len(selected) == limit:
            break
        if id(item) not in selected_ids:
            selected.append(item)
            selected_ids.add(id(item))
    selected.sort(key=ranking)
    for item in selected:
        item["evidence_count"] = len(item["evidence"])
    return selected


def merge_web(candidates, web_candidates, limit):
    by_source = {item["source_id"]: item for item in candidates if item.get("source_id")}
    by_issn = {}
    ambiguous_issns = set()
    for candidate in candidates:
        for issn in candidate.get("issns", []):
            if issn in by_issn and by_issn[issn] is not candidate:
                ambiguous_issns.add(issn)
            else:
                by_issn[issn] = candidate
    for issn in ambiguous_issns:
        del by_issn[issn]
    supplemental = []
    rejected = []
    for item in web_candidates:
        match = by_source.get(item.get("source_id")) if item.get("source_id") else None
        if match is None:
            match = next((by_issn[issn] for issn in item["issns"] if issn in by_issn), None)
        if match is not None and item.get("source_id") and match.get("source_id") \
                and item["source_id"] != match["source_id"]:
            rejected.append({"issn": item["web_input_issn"],
                             "reason": "Conflicting OpenAlex source identities"})
            continue
        evidence = {"issn": item["web_input_issn"], "provider": item["registry_provider"],
                    "response_sha256": item["registry_response_sha256"]}
        if match is not None:
            if item.get("source_id") and not match.get("source_id"):
                match["source_id"] = item["source_id"]
                match["issn_l"] = item["issn_l"]
                match["source_works_count"] = item["source_works_count"]
                if not match.get("homepage_url"):
                    match["homepage_url"] = item["homepage_url"]
                by_source[item["source_id"]] = match
            if "web_registry_verified" not in match["evidence"]:
                match["evidence"].append("web_registry_verified")
            match.setdefault("web_results_unverified", []).append(item["web_result_url_unverified"])
            match.setdefault("web_registry_matches", []).append(evidence)
        else:
            supplemental.append({**item, "matching_works": None, "topic_share": None,
                                 "evidence": ["web_registry_verified"],
                                 "web_results_unverified": [item["web_result_url_unverified"]],
                                 "web_registry_matches": [evidence]})
            del supplemental[-1]["web_result_url_unverified"]
            del supplemental[-1]["registry_provider"]
            del supplemental[-1]["registry_response_sha256"]
            del supplemental[-1]["web_input_issn"]
            if item.get("source_id"):
                by_source[item["source_id"]] = supplemental[-1]
            for issn in item["issns"]:
                if issn not in ambiguous_issns:
                    if issn in by_issn and by_issn[issn] is not supplemental[-1]:
                        del by_issn[issn]
                        ambiguous_issns.add(issn)
                    else:
                        by_issn[issn] = supplemental[-1]
    all_candidates = candidates + supplemental
    def ranking(item):
        return (-len(item["evidence"]), -(item.get("topic_share") or 0),
                -(item.get("reference_works") or 0), -(item.get("citing_works") or 0),
                -(item.get("matching_works") or 0), item["display_name"].casefold())
    reserved = []
    for signal in ("doaj_subject", "citation_graph", "web_registry_verified"):
        pool = sorted((item for item in all_candidates if item["evidence"] == [signal]), key=ranking)
        reserved.extend(pool[:min(limit // 5, len(pool))])
    selected = []
    seen = set()
    for item in reserved + sorted(all_candidates, key=ranking):
        if len(selected) == limit:
            break
        if id(item) not in seen:
            selected.append(item)
            seen.add(id(item))
    selected.sort(key=ranking)
    for item in selected:
        item["evidence_count"] = len(item["evidence"])
    return selected, rejected


def discover(query, output_root, per_page=50, limit=50, opener=urlopen, field_id=None,
             published_after=None, strategy="grouped", doaj_subject=None, citation_record=None,
             web_candidates=None):
    query = query.strip()
    if not query or len(query) > 200 or any(character in query for character in "\r\n\x00"):
        raise ValueError("--query must be a single topic phrase of 1 to 200 characters")
    if not 1 <= per_page <= 100 or not 1 <= limit <= 100:
        raise ValueError("--works and --limit must be between 1 and 100")
    if strategy not in {"grouped", "sample"}:
        raise ValueError("--strategy must be grouped or sample")
    if citation_record is not None and strategy != "grouped":
        raise ValueError("--citation-record requires --strategy grouped")
    if web_candidates is not None and strategy != "grouped":
        raise ValueError("--web-candidates requires --strategy grouped")
    if doaj_subject is not None:
        if strategy != "grouped":
            raise ValueError("--doaj-subject requires --strategy grouped")
        if (not isinstance(doaj_subject, str) or not 1 <= len(doaj_subject.strip()) <= 100
                or not all(character.isalnum() or character in " -" for character in doaj_subject)):
            raise ValueError("--doaj-subject must be a nonconfidential subject phrase of 1 to 100 characters")
        doaj_subject = doaj_subject.strip()
    if field_id is not None and (isinstance(field_id, bool) or not isinstance(field_id, int) or field_id <= 0):
        raise ValueError("--field-id must be a positive OpenAlex field ID")
    if published_after is not None:
        if not isinstance(published_after, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", published_after):
            raise ValueError("--published-after must be a YYYY-MM-DD date")
        try:
            date.fromisoformat(published_after)
        except ValueError as error:
            raise ValueError("--published-after is not a valid date") from error
    root = output_root.expanduser()
    outside_git(root)
    if citation_record is not None:
        citation_candidates, citation_hash, reference_input_hash = read_citation_record(citation_record)
    else:
        citation_candidates, citation_hash, reference_input_hash = None, None, None
    if web_candidates is not None:
        web_entries, web_input_hash = read_web_candidates(web_candidates)
    else:
        web_entries, web_input_hash = None, None
    if strategy == "grouped":
        candidates, response_hash, groups_seen, matching_works = grouped_shortlist(
            query, 100 if doaj_subject or citation_record or web_entries else limit, opener, field_id, published_after)
        if doaj_subject:
            doaj_candidates, doaj_hash, doaj_total, doaj_seen = fetch_doaj_subject(doaj_subject, opener)
            candidates = merge_doaj(candidates, doaj_candidates, 100 if citation_record or web_entries else limit)
        else:
            doaj_hash = doaj_total = doaj_seen = None
            for candidate in candidates:
                candidate["evidence_count"] = 1
        if citation_record is not None:
            candidates = merge_citation(candidates, citation_candidates, 100 if web_entries else limit)
        if web_entries is not None:
            verified_web, rejected_web, web_checks = verify_web_candidates(web_entries, opener)
            candidates, merge_rejected = merge_web(candidates, verified_web, limit)
            rejected_web += merge_rejected
            web_providers = sorted({attempt["provider"] for item in web_checks
                                    for attempt in item["registry_checks"]})
        else:
            verified_web = rejected_web = None
            web_checks = None
            web_providers = []
    else:
        payload, response_hash = fetch_works(query, per_page, opener, field_id, published_after)
        candidates = shortlist(payload, limit)
        doaj_hash = doaj_total = doaj_seen = None
        verified_web = rejected_web = None
        web_checks = None
        web_providers = []
    root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100000):
        directory = root / f"discovery-{number}"
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise ValueError("No available discovery number")
    work_filter = "article"
    if strategy == "grouped":
        work_filter += " with journal primary location"
    if field_id is not None:
        work_filter += " and matching primary field"
    coverage_note = "First OpenAlex group page only"
    if doaj_subject:
        coverage_note += "; first DOAJ subject result page only"
    if citation_record is not None:
        coverage_note += "; selected reference DOIs and first citing-journal group page only"
    if web_entries is not None:
        coverage_note += "; manually selected web candidates checked by ISSN registry, not by editorial scope"
    coverage_note += "; candidate coverage is incomplete"
    record = {
        "schema_version": 4,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "providers": list(dict.fromkeys(["OpenAlex"] + (["DOAJ"] if doaj_subject else []) + web_providers)),
        "query": query,
        "field_id": field_id,
        "published_after": published_after,
        "work_filter": work_filter,
        "strategy": strategy,
        "works_requested": per_page if strategy == "sample" else None,
        "grouped_sources_seen": groups_seen if strategy == "grouped" else None,
        "matching_works_total": matching_works if strategy == "grouped" else None,
        "coverage_note": coverage_note if strategy == "grouped" else "Sampled works only; candidate coverage is incomplete",
        "topic_share_denominator": "OpenAlex source works_count across all years; not restricted to the search date or article type" if strategy == "grouped" else None,
        "selection_note": "Candidates with more discovery strategies are listed first; up to one fifth of slots each are reserved for DOAJ-only, citation-only, and registry-verified web-only journals. Neither order nor count predicts acceptance." if doaj_subject or citation_record is not None or web_entries is not None else None,
        "response_sha256": response_hash,
        "doaj_subject": doaj_subject,
        "doaj_results_total": doaj_total,
        "doaj_results_seen": doaj_seen,
        "doaj_response_sha256": doaj_hash,
        "citation_record_sha256": citation_hash,
        "citation_reference_input_sha256": reference_input_hash,
        "web_input_sha256": web_input_hash,
        "web_submitted_count": len(web_entries) if web_entries is not None else None,
        "web_verified_count": len(verified_web) if verified_web is not None else None,
        "web_registry_checks": web_checks,
        "web_rejected": rejected_web,
        "candidates": candidates,
    }
    output = directory / "candidates.json"
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Explicit topic phrase; do not use confidential manuscript text")
    parser.add_argument("--output-root", type=Path, default=Path.home() / "manuscript-audit" / "discovery")
    parser.add_argument("--works", type=int, default=50, help="Works to inspect with --strategy sample (1-100)")
    parser.add_argument("--limit", type=int, default=50, help="Maximum journal candidates to retain (1-100)")
    parser.add_argument("--strategy", choices=("grouped", "sample"), default="grouped")
    parser.add_argument("--doaj-subject", help="Optional nonconfidential DOAJ subject phrase; uses the first journal result page")
    parser.add_argument("--citation-record", type=Path, help="Optional private citation-candidates.json from selected reference DOIs")
    parser.add_argument("--web-candidates", type=Path, help="Optional private ISSN and web-result URL pairs for registry verification")
    parser.add_argument("--field-id", type=int, help="Optional OpenAlex primary field ID")
    parser.add_argument("--published-after", help="Keep only works published on or after this YYYY-MM-DD date")
    args = parser.parse_args()
    try:
        print(discover(args.query, args.output_root, args.works, args.limit,
                       field_id=args.field_id, published_after=args.published_after,
                       strategy=args.strategy, doaj_subject=args.doaj_subject,
                       citation_record=args.citation_record, web_candidates=args.web_candidates))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
