package issuer

import (
	"bytes"
	"crypto/ed25519"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"os"
	"path/filepath"
	"testing"
)

func TestCanonicalJSONMatchesPythonEncoding(t *testing.T) {
	value := map[string]any{
		"signature_algorithm": "Ed25519",
		"schema_version":      1,
		"payload": map[string]any{
			"installation_limit": 2,
			"customer_name":      "研发 & 测试",
		},
	}
	actual, err := canonicalJSON(value)
	if err != nil {
		t.Fatal(err)
	}
	expected := `{"payload":{"customer_name":"研发 & 测试","installation_limit":2},"schema_version":1,"signature_algorithm":"Ed25519"}`
	if string(actual) != expected {
		t.Fatalf("canonical JSON mismatch\nwant: %s\n got: %s", expected, string(actual))
	}
}

func TestEd25519SignatureVectorMatchesBackend(t *testing.T) {
	seed := make([]byte, ed25519.SeedSize)
	for index := range seed {
		seed[index] = byte(index)
	}
	message := []byte(`{"payload":{"customer_name":"研发 & 测试","installation_limit":2},"schema_version":1,"signature_algorithm":"Ed25519"}`)
	signature := ed25519.Sign(ed25519.NewKeyFromSeed(seed), message)
	actual := base64.RawURLEncoding.EncodeToString(signature)
	expected := "9wAlmv_NVXuXMiBUbg7YsyGVq5TsYXhtjH3KwJz8qiollLUyEn9zPVdrIvneJLdeQmE6zx7A46t_6hBZoJ_rAw"
	if actual != expected {
		t.Fatalf("signature vector mismatch\nwant: %s\n got: %s", expected, actual)
	}
}

func TestPythonExportedIssuerKeyMatchesPublicKey(t *testing.T) {
	issuerDir := os.Getenv("PCIDS_TEST_ISSUER_DIR")
	publicKeyPath := os.Getenv("PCIDS_TEST_PUBLIC_KEY")
	if issuerDir == "" || publicKeyPath == "" {
		t.Skip("external issuer compatibility paths are not configured")
	}
	privateKey, err := loadPrivateKey(issuerDir)
	if err != nil {
		t.Fatal(err)
	}
	publicPEM, err := os.ReadFile(publicKeyPath)
	if err != nil {
		t.Fatal(err)
	}
	block, _ := pem.Decode(publicPEM)
	if block == nil {
		t.Fatal("public key PEM is invalid")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	publicKey, ok := parsed.(ed25519.PublicKey)
	if !ok {
		t.Fatal("public key is not Ed25519")
	}
	if !bytes.Equal(privateKey.Public().(ed25519.PublicKey), publicKey) {
		t.Fatal("exported Windows private key does not match the app public key")
	}
}

func TestIssueLicenseTracksUniqueMachinesAndSignsOutput(t *testing.T) {
	issuerSource := os.Getenv("PCIDS_TEST_ISSUER_DIR")
	publicKeyPath := os.Getenv("PCIDS_TEST_PUBLIC_KEY")
	if issuerSource == "" || publicKeyPath == "" {
		t.Skip("external issuer compatibility paths are not configured")
	}
	temporary := t.TempDir()
	issuerDir := filepath.Join(temporary, "issuer")
	if err := os.MkdirAll(issuerDir, 0o700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{PrivateKeyFileName, PasswordFileName} {
		content, err := os.ReadFile(filepath.Join(issuerSource, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(issuerDir, name), content, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	issue := func(dataRoot string) (IssueResult, error) {
		return IssueLicense(IssueOptions{
			IssuerDir: issuerDir, DataRoot: dataRoot,
			CustomerID: "TEST-CUSTOMER", CustomerName: "测试客户",
			InstallationLimit: 2,
		})
	}
	first, err := issue(filepath.Join(temporary, "machine-1"))
	if err != nil {
		t.Fatal(err)
	}
	reissue, err := issue(filepath.Join(temporary, "machine-1"))
	if err != nil {
		t.Fatal(err)
	}
	if first.InstallationNo != 1 || reissue.InstallationNo != 1 {
		t.Fatalf("same machine should retain slot 1: first=%d reissue=%d", first.InstallationNo, reissue.InstallationNo)
	}
	second, err := issue(filepath.Join(temporary, "machine-2"))
	if err != nil {
		t.Fatal(err)
	}
	if second.InstallationNo != 2 {
		t.Fatalf("second machine should receive slot 2, got %d", second.InstallationNo)
	}
	if _, err := issue(filepath.Join(temporary, "machine-3")); err == nil {
		t.Fatal("third unique machine should be rejected by the two-machine limit")
	}

	content, err := os.ReadFile(second.LicensePath)
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(content, &document); err != nil {
		t.Fatal(err)
	}
	signatureText, _ := document["signature"].(string)
	signature, err := base64.RawURLEncoding.DecodeString(signatureText)
	if err != nil {
		t.Fatal(err)
	}
	envelope := map[string]any{
		"schema_version":      document["schema_version"],
		"signature_algorithm": document["signature_algorithm"],
		"payload":             document["payload"],
	}
	message, err := canonicalJSON(envelope)
	if err != nil {
		t.Fatal(err)
	}
	publicPEM, err := os.ReadFile(publicKeyPath)
	if err != nil {
		t.Fatal(err)
	}
	block, _ := pem.Decode(publicPEM)
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		t.Fatal(err)
	}
	if !ed25519.Verify(parsed.(ed25519.PublicKey), message, signature) {
		t.Fatal("generated license signature is not accepted by the app public key")
	}
}
