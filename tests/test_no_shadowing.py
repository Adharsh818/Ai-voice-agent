"""
No module defines the same top-level function or class twice (12 Oct: a second
server._services, for /health, replaced the one that builds each call's
services, and every phone call failed at the greeting).
"""

import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {".venv", "harness_runs", "tests", ".claude", "node_modules", "__pycache__"}


class NoShadowingTests(unittest.TestCase):
    def test_no_top_level_name_is_defined_twice(self):
        problems = []
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), path)
                seen = set()
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.name in seen:
                            problems.append(f"{os.path.relpath(path, ROOT)}: {node.name} (line {node.lineno})")
                        seen.add(node.name)
        self.assertEqual(problems, [])

    def test_phone_and_browser_calls_get_their_services(self):
        import inspect
        import server
        self.assertEqual(list(inspect.signature(server._services).parameters), ["app_state"])


if __name__ == "__main__":
    unittest.main()
