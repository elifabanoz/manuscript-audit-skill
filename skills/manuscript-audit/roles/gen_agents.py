"""Generate the two host formats from the dependency-free role catalog."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


CATALOG = Path(__file__).with_name("roles.yaml")
NAME = re.compile(r"audit-[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def load_roles() -> list[dict[str, object]]:
    # JSON is a YAML 1.2 subset and keeps generation dependency-free.
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if set(catalog) != {"schema_version", "roles"} or catalog["schema_version"] != 1:
        raise ValueError("roles.yaml has an unsupported catalog version or fields")
    roles = catalog["roles"]
    if not isinstance(roles, list) or not roles:
        raise ValueError("roles.yaml must contain roles")
    names: set[str] = set()
    for role in roles:
        if not isinstance(role, dict) or set(role) != {"name", "description", "instructions"}:
            raise ValueError("each role must have name, description, and instructions")
        name, description, instructions = role["name"], role["description"], role["instructions"]
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise ValueError(f"invalid or duplicate role name: {name}")
        if not isinstance(description, str) or not description.strip() or "\n" in description:
            raise ValueError(f"invalid description for {name}")
        if not isinstance(instructions, list) or not instructions or any(
            not isinstance(item, str) or not item.strip() or "\r" in item for item in instructions
        ):
            raise ValueError(f"invalid instructions for {name}")
        names.add(name)
    return roles


def contents(role: dict[str, object]) -> tuple[str, str]:
    name = role["name"]
    description = role["description"]
    body = "\n\n".join(role["instructions"])
    claude = (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "tools: Read\n"
        "---\n\n"
        f"{body}\n"
    )
    codex = (
        f"name = {json.dumps(name, ensure_ascii=False)}\n"
        f"description = {json.dumps(description, ensure_ascii=False)}\n"
        f"developer_instructions = {json.dumps(body, ensure_ascii=False)}\n\n"
        "[features]\n"
        "apps = false\n"
        "plugins = false\n"
        "memories = false\n"
        "request_permissions_tool = false\n"
    )
    return claude, codex


def generated(output_root: Path) -> dict[Path, str]:
    files = {}
    for role in load_roles():
        claude, codex = contents(role)
        files[output_root / ".claude" / "agents" / f"{role['name']}.md"] = claude
        files[output_root / ".codex" / "agents" / f"{role['name']}.toml"] = codex
    return files


def run(output_root: Path, check: bool, force: bool) -> None:
    files = generated(output_root)
    if check:
        stale = [str(path) for path, content in files.items()
                 if not path.is_file() or path.read_text(encoding="utf-8") != content]
        for directory, suffix in [
            (output_root / ".claude" / "agents", ".md"),
            (output_root / ".codex" / "agents", ".toml"),
        ]:
            if directory.is_dir():
                stale.extend(str(path) for path in directory.glob(f"audit-*{suffix}") if path not in files)
        if stale:
            raise ValueError("generated role files are missing or stale:\n" + "\n".join(stale))
        return
    for path, content in files.items():
        if path.exists() and path.read_text(encoding="utf-8") != content and not force:
            raise ValueError(f"refusing to overwrite a changed agent file: {path}; use --force")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--force", action="store_true", help="Replace changed generated files in --write mode")
    args = parser.parse_args()
    if args.force and not args.write:
        parser.error("--force requires --write")
    try:
        run(args.output_root.resolve(), args.check, args.force)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
