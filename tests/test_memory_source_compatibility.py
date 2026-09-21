"""Run with Python 3.11, the version used by the deployed service."""
import ast
import unittest
from pathlib import Path


class MemorySourceCompatibilityTests(unittest.TestCase):
    def test_memory_source_compiles_and_preserves_speaker_attribution(self):
        path = Path(__file__).parents[1] / "app/services/crm_memory_service.py"
        source = path.read_text()
        compile(source, str(path), "exec")
        tree = ast.parse(source)
        formatter = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == "_format_messages")
        scope = {"List": list, "Dict": dict}
        exec(compile(ast.Module(body=[formatter], type_ignores=[]), str(path), "exec"), scope)
        result = scope["_format_messages"]([
            {"id": "one", "is_from_contact": True, "content": "pergunta"},
            {"id": "two", "sender_id": "agent-1", "content": "resposta"},
            {"id": "three", "content": "sem autor"},
        ], {})
        self.assertIn("CLIENTE", result)
        self.assertIn("AGENTE agent-1", result)
        self.assertIn("AGENTE nao identificado", result)


if __name__ == "__main__":
    unittest.main()
