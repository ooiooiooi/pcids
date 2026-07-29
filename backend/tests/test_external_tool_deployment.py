import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_electron_prefers_installed_external_tools_before_staging_fallback():
    content = (ROOT / "electron" / "main.ts").read_text(encoding="utf-8")

    installed_burners = "path.join(process.resourcesPath, 'tools', 'burners')"
    staged_burners = "'D:\\\\PCIDS-Deploy\\\\burners'"
    installed_protocol = "path.join(process.resourcesPath, 'tools', 'protocol_adapters')"
    staged_protocol = "'D:\\\\PCIDS-Deploy\\\\protocol_adapters'"
    installed_codearts = "path.join(process.resourcesPath, 'tools', 'codearts_browser_runtime')"
    staged_codearts = "'D:\\\\PCIDS-Deploy\\\\codearts_browser_runtime'"

    assert content.index(installed_burners) < content.index(staged_burners)
    assert content.index(installed_protocol) < content.index(staged_protocol)
    assert content.index(installed_codearts) < content.index(staged_codearts)


def test_deployment_validates_and_registers_every_external_runtime():
    content = (ROOT / "scripts" / "deploy-target-workstation.ps1").read_text(encoding="utf-8")

    required_layout_markers = [
        r"ST-LINK\ST-LINK-Utility-CLI-3.6\ST-LINK_CLI.exe",
        r"J-LINK\JLink_V952\JLink.exe",
        r"SWD_Downloader\pyocd-runtime\Scripts\pyocd.exe",
        r"GDLINK\GD-LinkUtilityProgrammer_v2.1.24.40106\GD-LinkUtilityProgrammer\GDLink_CLI.exe",
        r"AL321\openFPGALoader\openFPGALoader.exe",
        r"GOWIN\bin\programmer_cli.exe",
        r"HDSC\hdsc_ccid_agent.py",
        r"HDC\OpenHarmony-6.1\toolchains\hdc.exe",
        r"XDS510plus\drivers\install-xds510plus-driver.ps1",
        r"USBCANFD-200U\sdk-manifest.json",
        r"CH347\ch347_gpio_probe.py",
        r"node_modules\node\bin\node.exe",
        r"node_modules\playwright\package.json",
    ]
    for marker in required_layout_markers:
        assert marker in content

    required_environment = [
        "PCIDS_BUNDLED_TOOLS_DIR",
        "PCIDS_PROTOCOL_ADAPTERS_DIR",
        "PCIDS_CODEARTS_WEB_RUNTIME",
        "PCIDS_BROWSER_EXECUTABLE",
        "STLINK_UTILITY_CLI",
        "JLINK_EXE",
        "PYOCD_EXE",
        "GDLINK_CLI",
        "OPENFPGALOADER_EXE",
        "GOWIN_PROGRAMMER_CLI",
        "HDSC_CCID_AGENT",
        "HDSC_CCID_V604_EXE",
        "HDSC_CCID_PYTHON",
        "HDC_EXE",
        "XDS510_DRIVER_INSTALL_SCRIPT",
        "PROGRAM_FLASH_EXE",
        "XSDB_EXE",
        "HW_SERVER_EXE",
        "IPECMD_EXE",
        "QUARTUS_PGM",
        "UNIFLASH_CLI",
        "DSS_BAT",
    ]
    for variable in required_environment:
        assert variable in content

    assert "Find-ExactFile (Join-Path $DeployRoot 'PCIDS') '*.exe'" in content
    assert "Find-CodeArtsBrowser" in content
    assert r"Microsoft\Edge\Application\msedge.exe" in content
    assert "CodeArts Web runtime requires bundled Chromium" in content
    assert "VersionInfo.FileVersion" in content
    assert content.index(r"Google\Chrome\Application\chrome.exe") < content.index(
        "Where-Object { $_.FullName -match 'playwright|chromium|browser' }"
    )


def test_win7_package_pins_last_compatible_electron_major():
    config = json.loads((ROOT / "electron-builder.win7-private.json").read_text(encoding="utf-8"))
    requirements = (ROOT / "requirements-win7-web.txt").read_text(encoding="utf-8")

    assert config["electronVersion"] == "22.3.27"
    assert config["directories"]["output"] == "release-win7-private"
    assert "cryptography==3.4.8" in requirements
    assert "bcrypt==3.2.2" in requirements
    assert any(item.get("from") == "backend/dist-win7-web" for item in config["extraResources"])
    resources = config["extraResources"]
    assert {
        "from": "tools/codearts_release_debugger/browser_runtime",
        "to": "tools/codearts_browser_runtime",
        "filter": ["**/*"],
    } in resources
    assert all(not str(item.get("to", "")).startswith("flash-adapter") for item in resources)


def test_driver_registration_uses_current_hdsc_directory_contract():
    content = (ROOT / "scripts" / "install-burner-drivers.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $Root "HDSC"' in content
    assert 'Join-Path $Root "HDSC_CCID"' not in content
    assert '"STLINK_UTILITY_CLI"' in content
    assert '"HDSC_CCID_AGENT"' in content
