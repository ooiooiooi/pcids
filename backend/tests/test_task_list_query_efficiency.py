import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base, Burner, BurningTask, Product, Repository, Script, User
from backend.routers.tasks import get_tasks


class TaskListQueryEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_page_relations_are_loaded_in_batches(self):
        user = User(username="operator", password_hash="unused", status=1)
        repository = Repository(
            name="firmware.bin",
            project_key="project-a",
            repo_detail_json=json.dumps({"name": "Project A"}),
        )
        burner = Burner(name="ST-LINK", type="ST-LINK")
        script = Script(name="flash", type="shell", content="echo ok")
        product = Product(name="board", chip_type="ARM")
        self.db.add_all([user, repository, burner, script, product])
        self.db.flush()
        self.db.add_all(
            [
                BurningTask(
                    task_no=f"20260810{index:03d}",
                    software_name=f"firmware-{index}",
                    created_by_user_id=user.id,
                    repository_id=repository.id,
                    burner_id=burner.id,
                    script_id=script.id,
                    product_id=product.id,
                    status=2,
                )
                for index in range(1, 21)
            ]
        )
        self.db.commit()

        statements = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", capture_statement)
        try:
            with patch(
                "backend.routers.tasks._repository_allowed_roots",
                return_value=["/tmp/pcids-test"],
            ) as allowed_roots:
                result = get_tasks(
                    page=1,
                    page_size=20,
                    status=None,
                    board_name=None,
                    keyword=None,
                    project_key=None,
                    sort_field=None,
                    sort_order="desc",
                    db=self.db,
                    current_user=SimpleNamespace(
                        id=user.id,
                        role=SimpleNamespace(data_scope="all"),
                    ),
                    _=None,
                )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(len(result["data"]), 20)
        self.assertLessEqual(len(statements), 8)
        allowed_roots.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
