package openbrowser

import (
	"errors"
	"os"
	"os/exec"
	"runtime"
)

// Open launches the operating-system browser without invoking a shell. The
// complete enrollment URL contains only a short-lived, non-secret user code;
// the device credential and private key never leave the recorder process.
func Open(target string) error {
	if os.Getenv("MESHAGENT_NO_BROWSER") == "1" {
		return errors.New("browser launch disabled")
	}
	var command *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		command = exec.Command("open", target)
	case "windows":
		command = exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", target)
	case "linux":
		command = exec.Command("xdg-open", target)
	default:
		return errors.New("browser launch is not supported on this platform")
	}
	return command.Start()
}
