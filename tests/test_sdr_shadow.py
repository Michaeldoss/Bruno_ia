import os
import unittest
from unittest.mock import patch
from app.services.sdr_shadow import evaluate_shadow


class ShadowSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "shadow_mode_disabled"):
                await evaluate_shadow(history=[{"role": "user", "content": "Procuro DTF"}])
