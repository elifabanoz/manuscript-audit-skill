import importlib.util
import tempfile
import tomllib
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "roles" / "gen_agents.py"
SPEC = importlib.util.spec_from_file_location("gen_agents", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RoleGenerationTests(unittest.TestCase):
    def test_generated_host_files_keep_same_role_instructions(self):
        roles = MODULE.load_roles()
        self.assertEqual(len(roles), 5)
        for role in roles:
            claude, codex = MODULE.contents(role)
            parsed = tomllib.loads(codex)
            self.assertEqual(parsed["name"], role["name"])
            self.assertEqual(parsed["description"], role["description"])
            self.assertEqual(parsed["developer_instructions"], "\n\n".join(role["instructions"]))
            self.assertEqual(claude.split("---\n\n", 1)[1], parsed["developer_instructions"] + "\n")
            self.assertIn("tools: Read\n", claude)
            self.assertFalse(parsed["features"]["apps"])
            self.assertFalse(parsed["features"]["plugins"])
            self.assertNotIn("model", parsed)

    def test_check_detects_changed_generated_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            MODULE.run(root, check=False, force=False)
            MODULE.run(root, check=True, force=False)
            agent = root / ".codex" / "agents" / "audit-defense.toml"
            agent.write_text(agent.read_text(encoding="utf-8") + "\nchanged = true\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing or stale"):
                MODULE.run(root, check=True, force=False)
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                MODULE.run(root, check=False, force=False)


if __name__ == "__main__":
    unittest.main()
