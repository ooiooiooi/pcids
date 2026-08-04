import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.utils.agent_security import build_agent_headers, get_agent_shared_token, require_agent_token


class AgentSecurityTests(unittest.TestCase):
    def test_matching_shared_token_is_accepted(self):
        request = SimpleNamespace(headers={"X-PCIDS-Agent-Token": "shared-secret"})
        with patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"):
            require_agent_token(request)

    def test_missing_shared_token_is_rejected(self):
        request = SimpleNamespace(headers={})
        with patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"):
            with self.assertRaisesRegex(HTTPException, "Agent 认证失败"):
                require_agent_token(request)

    def test_outgoing_agent_request_contains_shared_token(self):
        with patch("backend.utils.agent_security.get_agent_shared_token", return_value="shared-secret"):
            self.assertEqual(build_agent_headers(), {"X-PCIDS-Agent-Token": "shared-secret"})

    def test_external_machine_config_takes_priority_over_bundled_config(self):
        with TemporaryDirectory() as temp:
            config_path = Path(temp) / "agent.json"
            config_path.write_text(json.dumps({"shared_token": "machine-secret"}), encoding="utf-8")
            with patch.dict(os.environ, {"PCIDS_AGENT_CONFIG": str(config_path)}, clear=False):
                self.assertEqual(get_agent_shared_token(), "machine-secret")


if __name__ == "__main__":
    unittest.main()
