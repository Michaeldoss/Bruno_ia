"""Fail fast if the production requirements cannot support Bruno's Responses API.

Isolated CI intentionally installs latest openai, so this explicit contract
checks the version pinned for deployment instead of masking incompatibility.
"""
from pathlib import Path
import re
import unittest


class OpenAIRuntimeCompatibilityTests(unittest.TestCase):
    def test_production_sdk_supports_responses(self):
        requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
        match = re.search(r"(?m)^openai==([0-9]+)\.([0-9]+)\.([0-9]+)\s*$",
                          requirements.read_text())
        self.assertIsNotNone(match, "pin an OpenAI SDK version in requirements.txt")
        version = tuple(int(part) for part in match.groups())
        self.assertGreaterEqual(
            version, (1, 66, 0),
            "Bruno uses AsyncOpenAI.responses.create; production OpenAI SDK is too old",
        )
