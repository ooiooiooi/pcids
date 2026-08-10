from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.utils.license_manager import (
    LicenseError,
    canonical_json,
    encode_signature,
    format_utc,
    get_license_status,
    get_license_public_key_path,
    get_machine_identity,
    install_license_bytes,
    is_license_enforcement_enabled,
)
from scripts.license_issuer import initialize_issuer, issue_license


def _write_public_key(private_key: Ed25519PrivateKey, path: Path) -> None:
    path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def _signed_document(
    private_key: Ed25519PrivateKey,
    machine_fingerprint: str,
    now: datetime,
    expires_at: datetime | None = None,
) -> dict:
    payload = {
        "license_id": "LIC-TEST-001",
        "customer_id": "TEST",
        "customer_name": "测试客户",
        "product": "PCIDS",
        "machine_fingerprint": machine_fingerprint,
        "machine_code": "PCIDS-TEST",
        "installation_no": 1,
        "installation_limit": 3,
        "issued_at": format_utc(now),
        "not_before": format_utc(now - timedelta(minutes=1)),
        "expires_at": format_utc(expires_at) if expires_at else None,
        "features": ["core"],
    }
    envelope = {
        "schema_version": 1,
        "signature_algorithm": "Ed25519",
        "payload": payload,
    }
    return {
        **envelope,
        "signature": encode_signature(private_key.sign(canonical_json(envelope))),
    }


@pytest.fixture
def license_material(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PCIDS_LICENSE_ENFORCEMENT", "1")
    data_root = tmp_path / "data"
    public_key_path = tmp_path / "public.pem"
    private_key = Ed25519PrivateKey.generate()
    _write_public_key(private_key, public_key_path)
    identity = get_machine_identity(data_root)
    now = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
    return data_root, public_key_path, private_key, identity, now


def test_valid_machine_bound_license_is_accepted(license_material):
    data_root, public_key_path, private_key, identity, now = license_material
    license_path = data_root / "license" / "pcids.lic"
    license_path.write_text(
        json.dumps(_signed_document(private_key, identity["fingerprint"], now)),
        encoding="utf-8",
    )

    status = get_license_status(
        data_root=data_root,
        public_key_path=public_key_path,
        now=now,
    )

    assert status["valid"] is True
    assert status["state"] == "valid"
    assert status["license"]["installation_limit"] == 3


def test_tampered_payload_is_rejected(license_material):
    data_root, public_key_path, private_key, identity, now = license_material
    document = _signed_document(private_key, identity["fingerprint"], now)
    document["payload"]["installation_limit"] = 999
    license_path = data_root / "license" / "pcids.lic"
    license_path.write_text(json.dumps(document), encoding="utf-8")

    status = get_license_status(data_root, public_key_path=public_key_path, now=now)

    assert status["valid"] is False
    assert status["state"] == "invalid"
    assert "篡改" in status["message"]


def test_wrong_machine_and_expired_license_are_rejected(license_material):
    data_root, public_key_path, private_key, _identity, now = license_material
    license_path = data_root / "license" / "pcids.lic"
    license_path.write_text(
        json.dumps(_signed_document(private_key, "0" * 64, now)),
        encoding="utf-8",
    )
    wrong_machine = get_license_status(data_root, public_key_path=public_key_path, now=now)
    assert wrong_machine["state"] == "machine_mismatch"

    identity = get_machine_identity(data_root)
    license_path.write_text(
        json.dumps(
            _signed_document(
                private_key,
                identity["fingerprint"],
                now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            )
        ),
        encoding="utf-8",
    )
    expired = get_license_status(data_root, public_key_path=public_key_path, now=now)
    assert expired["state"] == "expired"


def test_import_always_verifies_signature_when_enforcement_is_disabled(
    license_material,
    monkeypatch: pytest.MonkeyPatch,
):
    data_root, public_key_path, _private_key, _identity, _now = license_material
    monkeypatch.setenv("PCIDS_LICENSE_ENFORCEMENT", "0")

    with pytest.raises(LicenseError, match="签名"):
        install_license_bytes(
            b'{"schema_version":1,"signature_algorithm":"Ed25519","payload":{},"signature":"bad"}',
            data_root=data_root,
            public_key_path=public_key_path,
        )

    assert not (data_root / "license" / "pcids.lic").exists()


def test_packaged_backend_cannot_disable_license_enforcement(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PCIDS_LICENSE_ENFORCEMENT", "0")
    monkeypatch.setenv("PCIDS_LICENSE_PUBLIC_KEY_FILE", "/tmp/attacker-public-key.pem")
    monkeypatch.setattr("backend.utils.license_manager.sys.frozen", True, raising=False)

    assert is_license_enforcement_enabled() is True
    assert get_license_public_key_path().name == "license_public_key.pem"
    assert str(get_license_public_key_path()) != "/tmp/attacker-public-key.pem"


def test_issuer_reuses_machine_slot_and_enforces_customer_limit(tmp_path: Path):
    issuer_dir = tmp_path / "issuer"
    public_key_path = tmp_path / "public.pem"
    initialize_issuer(issuer_dir, public_key_path)
    first_data_root = tmp_path / "machine-a"

    first = issue_license(
        issuer_dir,
        first_data_root,
        customer_id="CUSTOMER-A",
        customer_name="客户 A",
        installation_limit=1,
    )
    reissued = issue_license(
        issuer_dir,
        first_data_root,
        customer_id="CUSTOMER-A",
        customer_name="客户 A",
        installation_limit=1,
    )

    assert first["payload"]["installation_no"] == 1
    assert reissued["payload"]["installation_no"] == 1
    assert first["payload"]["license_id"] != reissued["payload"]["license_id"]

    with pytest.raises(ValueError, match="授权上限"):
        issue_license(
            issuer_dir,
            tmp_path / "machine-b",
            customer_id="CUSTOMER-A",
            customer_name="客户 A",
            installation_limit=1,
        )
