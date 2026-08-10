//go:build !windows

package issuer

import (
	"os"
	"runtime"
	"strings"
)

func platformMachineComponents() (map[string]string, error) {
	components := map[string]string{"platform": runtime.GOOS}
	if hostname, err := os.Hostname(); err == nil && strings.TrimSpace(hostname) != "" {
		components["node"] = hostname
	}
	for _, path := range []string{"/etc/machine-id", "/var/lib/dbus/machine-id"} {
		content, err := os.ReadFile(path)
		if err == nil && strings.TrimSpace(string(content)) != "" {
			components["machine_id"] = strings.TrimSpace(string(content))
			break
		}
	}
	return components, nil
}
