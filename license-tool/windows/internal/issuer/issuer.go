package issuer

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	ProductCode        = "PCIDS"
	LicenseFileName    = "pcids.lic"
	PrivateKeyFileName = "issuer_private_key.pcissuer"
	PasswordFileName   = "issuer_password.txt"
	LedgerFileName     = "issuer_ledger.json"
	keyContext         = "PCIDS-License-Issuer-v1"
)

type MachineIdentityInfo struct {
	Fingerprint string
	MachineCode string
}

type IssueOptions struct {
	IssuerDir         string
	DataRoot          string
	CustomerID        string
	CustomerName      string
	InstallationLimit int
	ExpiresOn         string
}

type IssueResult struct {
	LicensePath       string
	LicenseID         string
	MachineCode       string
	InstallationNo    int
	InstallationLimit int
}

type encryptedKeyContainer struct {
	Version    int    `json:"version"`
	Product    string `json:"product"`
	KDF        string `json:"kdf"`
	Iterations int    `json:"iterations"`
	Salt       string `json:"salt"`
	Nonce      string `json:"nonce"`
	Ciphertext string `json:"ciphertext"`
}

type ledgerIssue struct {
	CustomerID         string `json:"customer_id"`
	CustomerName       string `json:"customer_name"`
	MachineFingerprint string `json:"machine_fingerprint"`
	MachineCode        string `json:"machine_code"`
	InstallationNo     int    `json:"installation_no"`
	InstallationLimit  int    `json:"installation_limit"`
	LicenseID          string `json:"license_id"`
	IssuedAt           string `json:"issued_at"`
	ExpiresAt          any    `json:"expires_at"`
}

type ledger struct {
	Version int           `json:"version"`
	Issues  []ledgerIssue `json:"issues"`
}

func canonicalJSON(value any) ([]byte, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(buffer.Bytes(), []byte("\n")), nil
}

func decodeBase64(value string) ([]byte, error) {
	decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(value))
	if err != nil {
		return nil, fmt.Errorf("签发密钥编码无效: %w", err)
	}
	return decoded, nil
}

func pbkdf2SHA256(password, salt []byte, iterations, keyLength int) []byte {
	hashLength := sha256.Size
	blockCount := (keyLength + hashLength - 1) / hashLength
	result := make([]byte, 0, blockCount*hashLength)
	for block := 1; block <= blockCount; block++ {
		mac := hmac.New(sha256.New, password)
		mac.Write(salt)
		mac.Write([]byte{byte(block >> 24), byte(block >> 16), byte(block >> 8), byte(block)})
		u := mac.Sum(nil)
		t := append([]byte(nil), u...)
		for index := 1; index < iterations; index++ {
			mac = hmac.New(sha256.New, password)
			mac.Write(u)
			u = mac.Sum(nil)
			for offset := range t {
				t[offset] ^= u[offset]
			}
		}
		result = append(result, t...)
	}
	return result[:keyLength]
}

func loadPrivateKey(issuerDir string) (ed25519.PrivateKey, error) {
	containerPath := filepath.Join(issuerDir, PrivateKeyFileName)
	passwordPath := filepath.Join(issuerDir, PasswordFileName)
	containerBytes, err := os.ReadFile(containerPath)
	if err != nil {
		return nil, fmt.Errorf("未找到加密签发私钥 %s", containerPath)
	}
	passwordBytes, err := os.ReadFile(passwordPath)
	if err != nil {
		return nil, fmt.Errorf("未找到签发密码 %s", passwordPath)
	}
	password := []byte(strings.TrimSpace(string(passwordBytes)))
	if len(password) < 16 {
		return nil, errors.New("签发密码长度不足")
	}

	var container encryptedKeyContainer
	if err := json.Unmarshal(containerBytes, &container); err != nil {
		return nil, fmt.Errorf("加密签发私钥格式无效: %w", err)
	}
	if container.Version != 1 || container.Product != ProductCode || container.KDF != "PBKDF2-HMAC-SHA256" {
		return nil, errors.New("加密签发私钥版本不受支持")
	}
	if container.Iterations < 100000 {
		return nil, errors.New("加密签发私钥的派生强度不足")
	}
	salt, err := decodeBase64(container.Salt)
	if err != nil {
		return nil, err
	}
	nonce, err := decodeBase64(container.Nonce)
	if err != nil {
		return nil, err
	}
	ciphertext, err := decodeBase64(container.Ciphertext)
	if err != nil {
		return nil, err
	}
	key := pbkdf2SHA256(password, salt, container.Iterations, 32)
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	seed, err := gcm.Open(nil, nonce, ciphertext, []byte(keyContext))
	if err != nil {
		return nil, errors.New("签发私钥或密码不正确")
	}
	if len(seed) != ed25519.SeedSize {
		return nil, errors.New("签发私钥长度无效")
	}
	return ed25519.NewKeyFromSeed(seed), nil
}

func ensureInstallationID(dataRoot string) (string, error) {
	licenseDir := filepath.Join(dataRoot, "license")
	if err := os.MkdirAll(licenseDir, 0o755); err != nil {
		return "", fmt.Errorf("无法创建 License 目录: %w", err)
	}
	path := filepath.Join(licenseDir, "installation_id")
	if content, err := os.ReadFile(path); err == nil {
		value := strings.TrimSpace(string(content))
		if value != "" {
			return value, nil
		}
	}
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return "", err
	}
	value := hex.EncodeToString(random)
	if err := atomicWrite(path, []byte(value), 0o600); err != nil {
		return "", err
	}
	return value, nil
}

func MachineIdentity(dataRoot string) (MachineIdentityInfo, error) {
	dataRoot = strings.TrimSpace(dataRoot)
	if dataRoot == "" {
		return MachineIdentityInfo{}, errors.New("请选择 PCIDS 的 data 目录")
	}
	installationID, err := ensureInstallationID(dataRoot)
	if err != nil {
		return MachineIdentityInfo{}, err
	}
	components := map[string]string{
		"installation_id": strings.ToLower(strings.TrimSpace(installationID)),
	}
	machineComponents, err := platformMachineComponents()
	if err != nil {
		return MachineIdentityInfo{}, err
	}
	for key, value := range machineComponents {
		if normalized := strings.ToLower(strings.TrimSpace(value)); normalized != "" {
			components[key] = normalized
		}
	}
	canonical, err := canonicalJSON(components)
	if err != nil {
		return MachineIdentityInfo{}, err
	}
	digest := sha256.Sum256(canonical)
	fingerprint := hex.EncodeToString(digest[:])
	return MachineIdentityInfo{
		Fingerprint: fingerprint,
		MachineCode: formatMachineCode(fingerprint),
	}, nil
}

func formatMachineCode(fingerprint string) string {
	visible := strings.ToUpper(fingerprint)
	if len(visible) > 24 {
		visible = visible[:24]
	}
	parts := make([]string, 0, 6)
	for index := 0; index < len(visible); index += 4 {
		end := index + 4
		if end > len(visible) {
			end = len(visible)
		}
		parts = append(parts, visible[index:end])
	}
	return "PCIDS-" + strings.Join(parts, "-")
}

func loadLedger(path string) (ledger, error) {
	content, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return ledger{Version: 1, Issues: []ledgerIssue{}}, nil
	}
	if err != nil {
		return ledger{}, err
	}
	var value ledger
	if err := json.Unmarshal(content, &value); err != nil {
		return ledger{}, fmt.Errorf("授权台账格式无效: %w", err)
	}
	if value.Version != 1 {
		return ledger{}, errors.New("授权台账版本不受支持")
	}
	return value, nil
}

func reserveInstallation(value *ledger, customerID, customerName string, identity MachineIdentityInfo, limit int) (int, error) {
	for _, issue := range value.Issues {
		if issue.CustomerID == customerID && issue.MachineFingerprint == identity.Fingerprint {
			if limit < issue.InstallationNo {
				return 0, fmt.Errorf("授权机器总数不能小于当前机器序号 %d", issue.InstallationNo)
			}
			return issue.InstallationNo, nil
		}
	}
	count := 0
	maximum := 0
	for _, issue := range value.Issues {
		if issue.CustomerID == customerID {
			count++
			if issue.InstallationNo > maximum {
				maximum = issue.InstallationNo
			}
		}
	}
	if count >= limit {
		return 0, fmt.Errorf("客户 %s 已达到 %d 台机器的授权上限", customerName, limit)
	}
	return maximum + 1, nil
}

func parseExpiration(value string) (any, error) {
	raw := strings.TrimSpace(value)
	if raw == "" {
		return nil, nil
	}
	date, err := time.ParseInLocation("2006-01-02", raw, time.Local)
	if err != nil {
		return nil, errors.New("授权截止日期格式应为 YYYY-MM-DD")
	}
	expires := time.Date(date.Year(), date.Month(), date.Day(), 23, 59, 59, 0, time.Local).UTC()
	if !expires.After(time.Now().UTC()) {
		return nil, errors.New("授权截止日期必须晚于当前时间")
	}
	return expires.Format("2006-01-02T15:04:05Z"), nil
}

func randomLicenseID(now time.Time) (string, error) {
	random := make([]byte, 6)
	if _, err := rand.Read(random); err != nil {
		return "", err
	}
	return "LIC-" + now.UTC().Format("20060102") + "-" + strings.ToUpper(hex.EncodeToString(random)), nil
}

func upsertLedgerIssue(value *ledger, issue ledgerIssue) {
	for index := range value.Issues {
		if value.Issues[index].CustomerID == issue.CustomerID && value.Issues[index].MachineFingerprint == issue.MachineFingerprint {
			value.Issues[index] = issue
			return
		}
	}
	value.Issues = append(value.Issues, issue)
	sort.Slice(value.Issues, func(left, right int) bool {
		if value.Issues[left].CustomerID == value.Issues[right].CustomerID {
			return value.Issues[left].InstallationNo < value.Issues[right].InstallationNo
		}
		return value.Issues[left].CustomerID < value.Issues[right].CustomerID
	})
}

func IssueLicense(options IssueOptions) (IssueResult, error) {
	options.IssuerDir = strings.TrimSpace(options.IssuerDir)
	options.DataRoot = strings.TrimSpace(options.DataRoot)
	options.CustomerID = strings.TrimSpace(options.CustomerID)
	options.CustomerName = strings.TrimSpace(options.CustomerName)
	if options.IssuerDir == "" || options.DataRoot == "" {
		return IssueResult{}, errors.New("签发资料目录和软件 data 目录不能为空")
	}
	if options.CustomerID == "" || options.CustomerName == "" {
		return IssueResult{}, errors.New("客户编号和客户名称不能为空")
	}
	if options.InstallationLimit < 1 {
		return IssueResult{}, errors.New("授权机器总数必须大于 0")
	}
	privateKey, err := loadPrivateKey(options.IssuerDir)
	if err != nil {
		return IssueResult{}, err
	}
	identity, err := MachineIdentity(options.DataRoot)
	if err != nil {
		return IssueResult{}, err
	}
	ledgerPath := filepath.Join(options.IssuerDir, LedgerFileName)
	ledgerValue, err := loadLedger(ledgerPath)
	if err != nil {
		return IssueResult{}, err
	}
	installationNo, err := reserveInstallation(&ledgerValue, options.CustomerID, options.CustomerName, identity, options.InstallationLimit)
	if err != nil {
		return IssueResult{}, err
	}
	expiresAt, err := parseExpiration(options.ExpiresOn)
	if err != nil {
		return IssueResult{}, err
	}
	now := time.Now().UTC()
	issuedAt := now.Format("2006-01-02T15:04:05Z")
	licenseID, err := randomLicenseID(now)
	if err != nil {
		return IssueResult{}, err
	}
	payload := map[string]any{
		"license_id":          licenseID,
		"customer_id":         options.CustomerID,
		"customer_name":       options.CustomerName,
		"product":             ProductCode,
		"machine_fingerprint": identity.Fingerprint,
		"machine_code":        identity.MachineCode,
		"installation_no":     installationNo,
		"installation_limit":  options.InstallationLimit,
		"issued_at":           issuedAt,
		"not_before":          issuedAt,
		"expires_at":          expiresAt,
		"features":            []string{"core", "business_sync", "hardware_test"},
	}
	envelope := map[string]any{
		"schema_version":      1,
		"signature_algorithm": "Ed25519",
		"payload":             payload,
	}
	canonical, err := canonicalJSON(envelope)
	if err != nil {
		return IssueResult{}, err
	}
	document := map[string]any{
		"schema_version":      1,
		"signature_algorithm": "Ed25519",
		"payload":             payload,
		"signature":           base64.RawURLEncoding.EncodeToString(ed25519.Sign(privateKey, canonical)),
	}
	documentBytes, err := prettyJSON(document)
	if err != nil {
		return IssueResult{}, err
	}
	licensePath := filepath.Join(options.DataRoot, "license", LicenseFileName)
	if err := os.MkdirAll(filepath.Dir(licensePath), 0o755); err != nil {
		return IssueResult{}, err
	}
	upsertLedgerIssue(&ledgerValue, ledgerIssue{
		CustomerID: options.CustomerID, CustomerName: options.CustomerName,
		MachineFingerprint: identity.Fingerprint, MachineCode: identity.MachineCode,
		InstallationNo: installationNo, InstallationLimit: options.InstallationLimit,
		LicenseID: licenseID, IssuedAt: issuedAt, ExpiresAt: expiresAt,
	})
	ledgerBytes, err := prettyJSON(ledgerValue)
	if err != nil {
		return IssueResult{}, err
	}
	if err := atomicWrite(ledgerPath, ledgerBytes, 0o600); err != nil {
		return IssueResult{}, fmt.Errorf("无法保存授权台账: %w", err)
	}
	if err := atomicWrite(licensePath, documentBytes, 0o600); err != nil {
		return IssueResult{}, fmt.Errorf("无法写入 License: %w", err)
	}
	return IssueResult{
		LicensePath: licensePath, LicenseID: licenseID, MachineCode: identity.MachineCode,
		InstallationNo: installationNo, InstallationLimit: options.InstallationLimit,
	}, nil
}

func prettyJSON(value any) ([]byte, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

func atomicWrite(path string, content []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	temporary := path + "." + strconv.FormatInt(time.Now().UnixNano(), 10) + ".tmp"
	if err := os.WriteFile(temporary, content, mode); err != nil {
		return err
	}
	if err := os.Rename(temporary, path); err != nil {
		if _, statErr := os.Stat(path); statErr == nil {
			if removeErr := os.Remove(path); removeErr == nil {
				err = os.Rename(temporary, path)
			}
		}
		if err != nil {
			_ = os.Remove(temporary)
			return err
		}
	}
	return nil
}
