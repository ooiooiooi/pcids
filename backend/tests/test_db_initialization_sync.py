import unittest
from unittest.mock import MagicMock, patch

from backend.models.base import Base
from backend.utils import db as db_utils
from backend.utils.burner_automation import build_system_script_content


class DatabaseInitializationSyncTests(unittest.TestCase):
    def test_stlink_initialization_uses_utility_supported_frequency_defaults(self):
        by_name = {item["name"]: item for item in db_utils.DEFAULT_SYSTEM_SCRIPT_CATALOG}
        config = by_name["stlink_stm32_mcu_flash"]["default_config"]

        self.assertEqual(config["write_speed_khz"], 900)
        self.assertEqual(config["speed_options"], [125, 240, 480, 900, 1800, 4000])

    def test_al321_and_xds_defaults_use_timeout_seconds_for_ui_consistency(self):
        by_name = {item["name"]: item for item in db_utils.DEFAULT_SYSTEM_SCRIPT_CATALOG}
        self.assertEqual(by_name["al321_fpga_mcu_flash"]["default_config"]["timeout_seconds"], 1200)
        self.assertEqual(by_name["xds510plus_dsp_flash"]["default_config"]["timeout_seconds"], 600)

    def test_initialization_uses_the_normal_hdsc_script_generator(self):
        script_name = "hdsc_ccid_arm_mcu_flash"
        self.assertEqual(
            db_utils._build_system_script_content(script_name, "HDSC CCID"),
            build_system_script_content(script_name, "HDSC CCID"),
        )

    def test_existing_database_syncs_system_defaults_before_marking_initialized(self):
        session = MagicMock()
        with (
            patch.object(Base.metadata, "create_all"),
            patch.object(db_utils, "ensure_schema"),
            patch.object(db_utils, "_is_initial_seed_completed", return_value=False),
            patch.object(db_utils, "_has_existing_business_data", return_value=True),
            patch.object(db_utils, "SessionLocal", return_value=session),
            patch.object(db_utils, "_sync_recurring_application_data") as sync_migrations,
            patch.object(db_utils, "ensure_default_system_scripts") as ensure_scripts,
            patch.object(db_utils, "ensure_script_task_types") as ensure_task_types,
            patch.object(db_utils, "ensure_default_products") as ensure_products,
            patch.object(db_utils, "ensure_product_burn_interfaces") as ensure_interfaces,
            patch.object(db_utils, "_mark_initial_seed_completed") as mark_completed,
        ):
            db_utils.init_db()

        sync_migrations.assert_called_once_with(session)
        ensure_scripts.assert_called_once_with(session)
        ensure_task_types.assert_called_once_with(session)
        ensure_products.assert_called_once_with(session)
        ensure_interfaces.assert_called_once_with(session)
        session.close.assert_called_once_with()
        mark_completed.assert_called_once_with()

    def test_completed_database_still_runs_recurring_data_migrations(self):
        session = MagicMock()
        with (
            patch.object(Base.metadata, "create_all"),
            patch.object(db_utils, "ensure_schema"),
            patch.object(db_utils, "_is_initial_seed_completed", return_value=True),
            patch.object(db_utils, "SessionLocal", return_value=session),
            patch.object(db_utils, "_sync_recurring_application_data") as sync_migrations,
            patch.object(db_utils, "ensure_default_system_scripts"),
            patch.object(db_utils, "ensure_script_task_types"),
            patch.object(db_utils, "ensure_default_products"),
            patch.object(db_utils, "ensure_product_burn_interfaces"),
        ):
            db_utils.init_db()

        sync_migrations.assert_called_once_with(session)
        session.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
