"""Exercise the protected route without starting workers or contacting providers."""
import ast
import builtins
import hmac
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class Response:
    def __init__(self, content, status_code=200, headers=None):
        self.content, self.status_code = content, status_code


class MemoryDiagnosticsTests(unittest.TestCase):
    def invoke(self, key="operator", failure=False):
        source = ast.parse((Path(__file__).parents[1] / "app/main.py").read_text())
        route = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "memory_diagnostics")
        route.decorator_list = []
        scope = dict(Request=object, settings=SimpleNamespace(BRUNO_API_KEY="operator"),
                     hmac=hmac, os=os, JSONResponse=Response, memory_worker_alive=lambda: True)
        exec(compile(ast.Module(body=[route], type_ignores=[]), "memory_route", "exec"), scope)
        original_import = builtins.__import__

        def imports(name, *args, **kwargs):
            if name == "app.services.crm_memory_service":
                if failure:
                    raise ValueError("private environment value must never be exposed")
                return SimpleNamespace(ORG_ID="org", memory_budget_status=lambda: {"allowed": True})
            if name == "app.services.memory_diagnostics":
                return SimpleNamespace(cycle_snapshot=lambda org: {"status": "completed"})
            if name == "app.services.memory_tenant":
                return SimpleNamespace(require_org=lambda org: org)
            if name == "app.services":
                return SimpleNamespace(_segundos_ate_19h=lambda: 30)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", imports):
            return scope["memory_diagnostics"](SimpleNamespace(headers={"x-bruno-key": key}))

    def test_import_failure_is_diagnosed_without_secret_values(self):
        result = self.invoke(failure=True)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.content["error_type"], "ValueError")
        self.assertNotIn("private environment", str(result.content))
        self.assertIn("line", result.content["location"])

    def test_authentication_still_required(self):
        self.assertEqual(self.invoke(key="wrong", failure=True).status_code, 401)

    def test_success_shape_is_preserved(self):
        result = self.invoke()
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.content["worker_alive"])
        self.assertIn("org", result.content["cycles"])


if __name__ == "__main__":
    unittest.main()
