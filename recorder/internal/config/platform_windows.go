//go:build windows

package config

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/user"

	"golang.org/x/sys/windows"
)

func ApplyPrivateUmask() {}

func chmodPrivate(path string, mode os.FileMode) error {
	current, err := user.Current()
	if err != nil {
		return fmt.Errorf("resolve current Windows user for private ACL: %w", err)
	}
	if current.Uid == "" {
		return fmt.Errorf("resolve current Windows user for private ACL: SID is empty")
	}
	descriptor, err := windows.SecurityDescriptorFromString(
		fmt.Sprintf("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;%s)", current.Uid),
	)
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(
		path,
		windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION,
		nil,
		nil,
		dacl,
		nil,
	)
}

func socketPath(home string) string {
	identity := home
	if current, err := user.Current(); err == nil {
		identity = current.Uid
	}
	digest := sha256.Sum256([]byte(identity))
	return `\\.\pipe\meshagent-recorder-` + hex.EncodeToString(digest[:8])
}
