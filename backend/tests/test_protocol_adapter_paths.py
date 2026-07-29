from backend.utils import can_adapters


def test_protocol_adapter_root_prefers_explicit_runtime_directory(monkeypatch, tmp_path):
    explicit_root = tmp_path / "resources" / "tools" / "protocol_adapters"
    explicit_root.mkdir(parents=True)
    monkeypatch.setenv("PCIDS_PROTOCOL_ADAPTERS_DIR", str(explicit_root))
    monkeypatch.delenv("PCIDS_BUNDLED_TOOLS_DIR", raising=False)
    monkeypatch.delenv("PCIDS_RUNTIME_ROOT", raising=False)

    assert can_adapters._resolve_protocol_adapters_root() == explicit_root.resolve()


def test_protocol_adapter_root_uses_burner_tools_sibling_in_packaged_runtime(monkeypatch, tmp_path):
    tools_root = tmp_path / "resources" / "tools"
    burners_root = tools_root / "burners"
    protocol_root = tools_root / "protocol_adapters"
    burners_root.mkdir(parents=True)
    protocol_root.mkdir(parents=True)
    monkeypatch.delenv("PCIDS_PROTOCOL_ADAPTERS_DIR", raising=False)
    monkeypatch.setenv("PCIDS_BUNDLED_TOOLS_DIR", str(burners_root))
    monkeypatch.setenv("PCIDS_RUNTIME_ROOT", str(tmp_path))

    assert can_adapters._resolve_protocol_adapters_root() == protocol_root.resolve()
