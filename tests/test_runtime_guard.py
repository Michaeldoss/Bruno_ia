import os
import unittest
from unittest.mock import patch

from app.services.runtime_guard import is_pull_request_preview


class RuntimeGuardTests(unittest.TestCase):
    def test_render_preview_is_detected(self):
        with patch.dict(os.environ, {"IS_PULL_REQUEST": "true"}, clear=False):
            self.assertTrue(is_pull_request_preview())

    def test_production_is_not_preview(self):
        with patch.dict(os.environ, {"IS_PULL_REQUEST": "false"}, clear=False):
            self.assertFalse(is_pull_request_preview())


if __name__ == "__main__":
    unittest.main()
