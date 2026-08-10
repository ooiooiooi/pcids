import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException

from backend.routers import auth, users


class TokenRevocationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _db_for(user):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = user
        db.query.return_value.filter.return_value.count.return_value = 0
        return db

    async def test_current_token_version_is_accepted(self):
        user = SimpleNamespace(
            id=7,
            username="operator",
            token_version=3,
            status=1,
            last_active_at=None,
        )
        db = self._db_for(user)
        token = auth.create_access_token({"sub": user.username, "uid": user.id, "ver": 3})

        resolved = auth.get_current_user(token=token, db=db)
        resolved_again = auth.get_current_user(token=token, db=db)

        self.assertIs(resolved, user)
        self.assertIs(resolved_again, user)
        # Parallel page bootstrap requests must not turn every authenticated
        # SQLite read into a serialized write transaction.
        db.commit.assert_called_once_with()

    async def test_stale_or_legacy_token_is_rejected(self):
        user = SimpleNamespace(
            id=7,
            username="operator",
            token_version=4,
            status=1,
            last_active_at=None,
        )
        db = self._db_for(user)
        stale = auth.create_access_token({"sub": user.username, "uid": user.id, "ver": 3})
        legacy = auth.create_access_token({"sub": user.username, "uid": user.id})

        for token in (stale, legacy):
            with self.assertRaises(HTTPException) as context:
                auth.get_current_user(token=token, db=db)
            self.assertEqual(context.exception.status_code, 401)

    async def test_login_does_not_apply_a_concurrent_user_license_cap(self):
        user = SimpleNamespace(
            id=7,
            username="operator",
            token_version=0,
            status=1,
            last_active_at=None,
            verify_password=lambda password: password == "valid-password",
        )
        db = self._db_for(user)
        db.query.return_value.filter.return_value.count.return_value = 999
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
        form = SimpleNamespace(username=user.username, password="valid-password")

        result = await auth.login(request=request, form_data=form, db=db)

        self.assertEqual(result["token_type"], "bearer")
        self.assertTrue(result["access_token"])
        self.assertIsNotNone(user.last_active_at)
        db.query.return_value.filter.return_value.count.assert_not_called()

    async def test_logout_revokes_all_existing_tokens(self):
        user = SimpleNamespace(id=7, token_version=2, last_active_at=object())
        db = MagicMock()
        request = SimpleNamespace(headers={}, client=None)

        await auth.logout(request=request, db=db, current_user=user)

        self.assertEqual(user.token_version, 3)
        self.assertIsNone(user.last_active_at)
        db.commit.assert_called_once_with()

    async def test_kick_and_password_reset_increment_token_version(self):
        target = SimpleNamespace(id=8, token_version=5, last_active_at=object(), password_hash="old")
        operator = SimpleNamespace(id=7)
        db = self._db_for(target)

        await users.kick_user(user_id=8, db=db, current_user=operator, _=None)
        self.assertEqual(target.token_version, 6)

        await users.reset_password(user_id=8, db=db, current_user=operator, _=None)
        self.assertEqual(target.token_version, 7)
        self.assertNotEqual(target.password_hash, "old")


if __name__ == "__main__":
    unittest.main()
