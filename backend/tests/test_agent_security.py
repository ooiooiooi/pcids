import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.utils.agent_security import build_agent_headers, require_agent_token


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


if __name__ == "__main__":
    unittest.main()
