package main

import (
	"archive/zip"
	"bytes"
	_ "embed"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

//go:embed payload.zip
var payload []byte

const (
	appFolderName = "Dragonwilds Server Manager"
	appExeName    = "DragonwildsServerManager.exe"
	uninstallName = "Uninstall Dragonwilds Server Manager.exe"
	uninstallKey  = `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\DragonwildsServerManager`
)

var (
	user32         = syscall.NewLazyDLL("user32.dll")
	messageBoxProc = user32.NewProc("MessageBoxW")
)

func utf16(value string) *uint16 {
	ptr, _ := syscall.UTF16PtrFromString(value)
	return ptr
}

func messageBox(title, text string, flags uintptr) int {
	result, _, _ := messageBoxProc.Call(0, uintptr(unsafe.Pointer(utf16(text))), uintptr(unsafe.Pointer(utf16(title))), flags)
	return int(result)
}

func localAppData() string {
	if value := os.Getenv("LOCALAPPDATA"); value != "" {
		return value
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "AppData", "Local")
}

func installDir() string {
	return filepath.Join(localAppData(), "Programs", appFolderName)
}

func extractPayload(target string) error {
	reader, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		return fmt.Errorf("embedded application payload is invalid: %w", err)
	}
	if err := os.MkdirAll(target, 0755); err != nil {
		return err
	}
	cleanRoot, _ := filepath.Abs(target)
	for _, item := range reader.File {
		destination := filepath.Join(target, filepath.FromSlash(item.Name))
		cleanDest, _ := filepath.Abs(destination)
		if cleanDest != cleanRoot && !strings.HasPrefix(cleanDest, cleanRoot+string(os.PathSeparator)) {
			return fmt.Errorf("unsafe path in installer payload: %s", item.Name)
		}
		if item.FileInfo().IsDir() {
			if err := os.MkdirAll(destination, item.Mode()); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(destination), 0755); err != nil {
			return err
		}
		src, err := item.Open()
		if err != nil {
			return err
		}
		dst, err := os.OpenFile(destination, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, item.Mode())
		if err != nil {
			src.Close()
			return err
		}
		_, copyErr := io.Copy(dst, src)
		closeErr := dst.Close()
		src.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}

func copySelf(target string) error {
	source, err := os.Executable()
	if err != nil {
		return err
	}
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0755)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(output, input)
	closeErr := output.Close()
	if copyErr != nil {
		return copyErr
	}
	return closeErr
}

func psEscape(value string) string {
	return strings.ReplaceAll(value, "'", "''")
}

func runPowerShell(script string) error {
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if output, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("Windows shortcut setup failed: %v: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func createShortcuts(dir string) error {
	exe := filepath.Join(dir, appExeName)
	icon := filepath.Join(dir, "assets", "DragonwildsServerManager.ico")
	script := fmt.Sprintf(`
$ErrorActionPreference='Stop'
$ws=New-Object -ComObject WScript.Shell
$desktop=[Environment]::GetFolderPath('Desktop')
$start=[Environment]::GetFolderPath('Programs')
$targets=@(
  (Join-Path $desktop 'Dragonwilds Server Manager.lnk'),
  (Join-Path $start 'Dragonwilds Server Manager.lnk')
)
foreach($path in $targets){
  $s=$ws.CreateShortcut($path)
  $s.TargetPath='%s'
  $s.WorkingDirectory='%s'
  $s.IconLocation='%s,0'
  $s.Description='Dragonwilds dedicated server manager'
  $s.Save()
}
`, psEscape(exe), psEscape(dir), psEscape(icon))
	return runPowerShell(script)
}

func removeShortcuts() {
	_ = runPowerShell(`
$desktop=[Environment]::GetFolderPath('Desktop')
$start=[Environment]::GetFolderPath('Programs')
Remove-Item -LiteralPath (Join-Path $desktop 'Dragonwilds Server Manager.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $start 'Dragonwilds Server Manager.lnk') -Force -ErrorAction SilentlyContinue
`)
}

func registerUninstaller(dir string) error {
	uninstall := filepath.Join(dir, uninstallName)
	icon := filepath.Join(dir, "assets", "DragonwildsServerManager.ico")
	commands := [][]string{
		{"add", uninstallKey, "/v", "DisplayName", "/t", "REG_SZ", "/d", "Dragonwilds Server Manager", "/f"},
		{"add", uninstallKey, "/v", "DisplayVersion", "/t", "REG_SZ", "/d", "1.0.11", "/f"},
		{"add", uninstallKey, "/v", "Publisher", "/t", "REG_SZ", "/d", "Dragonwilds Server Manager", "/f"},
		{"add", uninstallKey, "/v", "InstallLocation", "/t", "REG_SZ", "/d", dir, "/f"},
		{"add", uninstallKey, "/v", "DisplayIcon", "/t", "REG_SZ", "/d", icon, "/f"},
		{"add", uninstallKey, "/v", "UninstallString", "/t", "REG_SZ", "/d", `"` + uninstall + `" --uninstall`, "/f"},
		{"add", uninstallKey, "/v", "NoModify", "/t", "REG_DWORD", "/d", "1", "/f"},
		{"add", uninstallKey, "/v", "NoRepair", "/t", "REG_DWORD", "/d", "1", "/f"},
	}
	for _, args := range commands {
		cmd := exec.Command("reg.exe", args...)
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
		if output, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("could not register uninstaller: %v: %s", err, strings.TrimSpace(string(output)))
		}
	}
	return nil
}

func provisionRuntime(dir string) error {
	launcher := filepath.Join(dir, appExeName)
	cmd := exec.Command(launcher, "--provision-runtime-only")
	cmd.Dir = dir
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("the application files were installed, but the private desktop runtime could not be provisioned: %w", err)
	}
	return nil
}

func launchApp(dir string) {
	cmd := exec.Command(filepath.Join(dir, appExeName))
	cmd.Dir = dir
	_ = cmd.Start()
}

func install() {
	if messageBox("Dragonwilds Server Manager Setup", "Install Dragonwilds Server Manager 1.0.11 for this Windows user?\n\nSetup will create a custom desktop shortcut and Start Menu shortcut.", 0x00000004|0x00000040) != 6 {
		return
	}
	dir := installDir()
	if err := os.RemoveAll(dir); err != nil {
		messageBox("Dragonwilds Server Manager Setup", "Could not replace the existing installation:\n"+err.Error(), 0x10)
		return
	}
	if err := extractPayload(dir); err != nil {
		messageBox("Dragonwilds Server Manager Setup", "Installation failed while extracting application files:\n"+err.Error(), 0x10)
		return
	}
	if err := copySelf(filepath.Join(dir, uninstallName)); err != nil {
		messageBox("Dragonwilds Server Manager Setup", "Application files were installed, but the uninstaller could not be created:\n"+err.Error(), 0x10)
		return
	}

	runtimeErr := provisionRuntime(dir)
	shortcutErr := createShortcuts(dir)
	registryErr := registerUninstaller(dir)

	if shortcutErr != nil || registryErr != nil {
		details := ""
		if shortcutErr != nil {
			details += "\nShortcut: " + shortcutErr.Error()
		}
		if registryErr != nil {
			details += "\nUninstaller registration: " + registryErr.Error()
		}
		messageBox("Dragonwilds Server Manager Setup", "The application was installed, but Windows integration was incomplete:"+details, 0x30)
	}
	if runtimeErr != nil {
		messageBox("Dragonwilds Server Manager Setup", runtimeErr.Error()+"\n\nThe desktop shortcut was still created. Launching it will retry private-runtime setup.", 0x30)
	} else {
		messageBox("Dragonwilds Server Manager Setup", "Installation complete.\n\nA custom Dragonwilds Server Manager shortcut was added to your desktop and Start Menu.", 0x40)
	}
	launchApp(dir)
}

func uninstall() {
	if messageBox("Uninstall Dragonwilds Server Manager", "Remove the Dragonwilds Server Manager application?\n\nServer profiles, backups, and application data will be preserved.", 0x00000004|0x00000030) != 6 {
		return
	}
	removeShortcuts()
	cmd := exec.Command("reg.exe", "delete", uninstallKey, "/f")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	_ = cmd.Run()

	dir := installDir()
	// Defer deletion until this running uninstaller exits.
	cleanup := fmt.Sprintf(`ping 127.0.0.1 -n 3 >nul & rmdir /S /Q "%s"`, strings.ReplaceAll(dir, `"`, `\"`))
	process := exec.Command("cmd.exe", "/C", cleanup)
	process.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	_ = process.Start()
	messageBox("Uninstall Dragonwilds Server Manager", "The application has been removed. Your server profiles, backups, and local application data were preserved.", 0x40)
}

func main() {
	exe, _ := os.Executable()
	base := strings.ToLower(filepath.Base(exe))
	for _, arg := range os.Args[1:] {
		if arg == "--uninstall" {
			uninstall()
			return
		}
	}
	if strings.HasPrefix(base, "uninstall") {
		uninstall()
		return
	}
	// Small pause prevents some security software from racing the freshly written payload.
	time.Sleep(100 * time.Millisecond)
	install()
}
