package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

const (
	installerURL  = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
	installerName = "python-3.12.10-amd64.exe"
	appName       = "DragonwildsServerManagerRebuild"

	wmCreate    = 0x0001
	wmDestroy   = 0x0002
	wmClose     = 0x0010
	wmSetFont   = 0x0030
	wmAppStatus = 0x8001
	wmAppDone   = 0x8002

	wsChild       = 0x40000000
	wsVisible     = 0x10000000
	wsCaption     = 0x00C00000
	wsSysMenu     = 0x00080000
	wsMinimizeBox = 0x00020000
	ssCenter      = 0x00000001
	ssCenterImage = 0x00000200

	swShow          = 5
	colorWindow     = 5
	defaultGUIFont  = 17
	idcArrow        = 32512
	maxRuntimeBytes = 100 * 1024 * 1024
)

type point struct {
	x int32
	y int32
}

type msg struct {
	hwnd     syscall.Handle
	message  uint32
	wParam   uintptr
	lParam   uintptr
	time     uint32
	pt       point
	lPrivate uint32
}

type wndClassEx struct {
	cbSize        uint32
	style         uint32
	lpfnWndProc   uintptr
	cbClsExtra    int32
	cbWndExtra    int32
	hInstance     syscall.Handle
	hIcon         syscall.Handle
	hCursor       syscall.Handle
	hbrBackground syscall.Handle
	lpszMenuName  *uint16
	lpszClassName *uint16
	hIconSm       syscall.Handle
}

var (
	user32               = syscall.NewLazyDLL("user32.dll")
	kernel32             = syscall.NewLazyDLL("kernel32.dll")
	gdi32                = syscall.NewLazyDLL("gdi32.dll")
	procMessageBoxW      = user32.NewProc("MessageBoxW")
	procRegisterClassExW = user32.NewProc("RegisterClassExW")
	procCreateWindowExW  = user32.NewProc("CreateWindowExW")
	procDefWindowProcW   = user32.NewProc("DefWindowProcW")
	procShowWindow       = user32.NewProc("ShowWindow")
	procUpdateWindow     = user32.NewProc("UpdateWindow")
	procGetMessageW      = user32.NewProc("GetMessageW")
	procTranslateMessage = user32.NewProc("TranslateMessage")
	procDispatchMessageW = user32.NewProc("DispatchMessageW")
	procPostMessageW     = user32.NewProc("PostMessageW")
	procPostQuitMessage  = user32.NewProc("PostQuitMessage")
	procDestroyWindow    = user32.NewProc("DestroyWindow")
	procSetWindowTextW   = user32.NewProc("SetWindowTextW")
	procSendMessageW     = user32.NewProc("SendMessageW")
	procLoadCursorW      = user32.NewProc("LoadCursorW")
	procGetSystemMetrics = user32.NewProc("GetSystemMetrics")
	procGetModuleHandleW = kernel32.NewProc("GetModuleHandleW")
	procGetStockObject   = gdi32.NewProc("GetStockObject")
	setupWindow          syscall.Handle
	statusControl        syscall.Handle
	setupMu              sync.Mutex
	setupStatus          = "Preparing the private desktop runtime…"
	setupErr             error
)

func utf16(value string) *uint16 {
	ptr, _ := syscall.UTF16PtrFromString(value)
	return ptr
}

func messageBox(title, text string, flags uintptr) {
	procMessageBoxW.Call(0, uintptr(unsafe.Pointer(utf16(text))), uintptr(unsafe.Pointer(utf16(title))), flags)
}

func fail(err error) {
	messageBox("Dragonwilds Server Manager", err.Error(), 0x10)
	os.Exit(1)
}

func exeDir() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	return filepath.Dir(exe), nil
}

func localData() string {
	if value := os.Getenv("LOCALAPPDATA"); value != "" {
		return value
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "AppData", "Local")
}

func setSetupStatus(text string) {
	setupMu.Lock()
	setupStatus = text
	setupMu.Unlock()
	if setupWindow != 0 {
		procPostMessageW.Call(uintptr(setupWindow), wmAppStatus, 0, 0)
	}
}

func currentSetupStatus() string {
	setupMu.Lock()
	defer setupMu.Unlock()
	return setupStatus
}

func wndProc(hwnd syscall.Handle, message uint32, wParam, lParam uintptr) uintptr {
	switch message {
	case wmCreate:
		staticClass := utf16("STATIC")
		text := utf16(currentSetupStatus())
		status, _, _ := procCreateWindowExW.Call(
			0,
			uintptr(unsafe.Pointer(staticClass)),
			uintptr(unsafe.Pointer(text)),
			wsChild|wsVisible|ssCenter|ssCenterImage,
			20, 18, 440, 104,
			uintptr(hwnd), 0, 0, 0,
		)
		statusControl = syscall.Handle(status)
		font, _, _ := procGetStockObject.Call(defaultGUIFont)
		if statusControl != 0 && font != 0 {
			procSendMessageW.Call(uintptr(statusControl), wmSetFont, font, 1)
		}
		return 0
	case wmAppStatus:
		if statusControl != 0 {
			procSetWindowTextW.Call(uintptr(statusControl), uintptr(unsafe.Pointer(utf16(currentSetupStatus()))))
		}
		return 0
	case wmAppDone:
		procDestroyWindow.Call(uintptr(hwnd))
		return 0
	case wmClose:
		setSetupStatus("Setup is still running. You can end it from Task Manager if you need to cancel.")
		return 0
	case wmDestroy:
		procPostQuitMessage.Call(0)
		return 0
	default:
		result, _, _ := procDefWindowProcW.Call(uintptr(hwnd), uintptr(message), wParam, lParam)
		return result
	}
}

func showSetupWindow(worker func() error) error {
	className := utf16("DragonwildsManagerSetupWindow")
	hInstance, _, _ := procGetModuleHandleW.Call(0)
	cursor, _, _ := procLoadCursorW.Call(0, idcArrow)
	class := wndClassEx{
		cbSize:        uint32(unsafe.Sizeof(wndClassEx{})),
		lpfnWndProc:   syscall.NewCallback(wndProc),
		hInstance:     syscall.Handle(hInstance),
		hCursor:       syscall.Handle(cursor),
		hbrBackground: syscall.Handle(colorWindow + 1),
		lpszClassName: className,
	}
	registered, _, registerErr := procRegisterClassExW.Call(uintptr(unsafe.Pointer(&class)))
	if registered == 0 {
		return fmt.Errorf("could not create the first-run setup window: %v", registerErr)
	}
	width, height := int32(480), int32(160)
	screenW, _, _ := procGetSystemMetrics.Call(0)
	screenH, _, _ := procGetSystemMetrics.Call(1)
	x := (int32(screenW) - width) / 2
	y := (int32(screenH) - height) / 2
	hwnd, _, createErr := procCreateWindowExW.Call(
		0,
		uintptr(unsafe.Pointer(className)),
		uintptr(unsafe.Pointer(utf16("Dragonwilds Server Manager — First-run setup"))),
		wsCaption|wsSysMenu|wsMinimizeBox,
		uintptr(x), uintptr(y), uintptr(width), uintptr(height),
		0, 0, hInstance, 0,
	)
	if hwnd == 0 {
		return fmt.Errorf("could not open the first-run setup window: %v", createErr)
	}
	setupWindow = syscall.Handle(hwnd)
	procShowWindow.Call(hwnd, swShow)
	procUpdateWindow.Call(hwnd)

	resultCh := make(chan error, 1)
	go func() {
		resultCh <- worker()
		procPostMessageW.Call(hwnd, wmAppDone, 0, 0)
	}()

	var message msg
	for {
		result, _, _ := procGetMessageW.Call(uintptr(unsafe.Pointer(&message)), 0, 0, 0)
		if int32(result) <= 0 {
			break
		}
		procTranslateMessage.Call(uintptr(unsafe.Pointer(&message)))
		procDispatchMessageW.Call(uintptr(unsafe.Pointer(&message)))
	}
	setupWindow = 0
	statusControl = 0
	return <-resultCh
}

func download(url, target string) error {
	client := &http.Client{Timeout: 15 * time.Minute}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "DragonwildsServerManagerRebuild/1.0.11")
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("could not download the private desktop runtime: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("runtime download returned HTTP %d", resp.StatusCode)
	}
	if resp.ContentLength > maxRuntimeBytes {
		return fmt.Errorf("runtime download exceeded the safety size limit")
	}
	tmp := target + ".part"
	out, err := os.Create(tmp)
	if err != nil {
		return err
	}
	defer out.Close()
	buffer := make([]byte, 256*1024)
	var done int64
	lastPercent := -1
	for {
		count, readErr := resp.Body.Read(buffer)
		if count > 0 {
			done += int64(count)
			if done > maxRuntimeBytes {
				out.Close()
				os.Remove(tmp)
				return fmt.Errorf("runtime download exceeded the safety size limit")
			}
			if _, writeErr := out.Write(buffer[:count]); writeErr != nil {
				out.Close()
				os.Remove(tmp)
				return writeErr
			}
			if resp.ContentLength > 0 {
				percent := int(done * 100 / resp.ContentLength)
				if percent != lastPercent {
					lastPercent = percent
					setSetupStatus(fmt.Sprintf("Downloading the signed private desktop runtime… %d%%", percent))
				}
			} else {
				setSetupStatus(fmt.Sprintf("Downloading the signed private desktop runtime… %.1f MB", float64(done)/(1024*1024)))
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			out.Close()
			os.Remove(tmp)
			return readErr
		}
	}
	if err := out.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	info, err := os.Stat(tmp)
	if err != nil || info.Size() < 20*1024*1024 {
		os.Remove(tmp)
		return fmt.Errorf("runtime download was incomplete")
	}
	return os.Rename(tmp, target)
}

func verifySignature(path string) error {
	setSetupStatus("Verifying the runtime publisher signature…")
	escaped := strings.ReplaceAll(path, "'", "''")
	script := fmt.Sprintf(`$s=Get-AuthenticodeSignature -FilePath '%s'; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Python Software Foundation'){exit 9}`, escaped)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("the downloaded runtime did not have a valid Python Software Foundation signature")
	}
	return nil
}

func installRuntime(installer, runtimeRoot string) error {
	setSetupStatus("Installing the private desktop runtime…")
	if err := os.RemoveAll(runtimeRoot); err != nil {
		return fmt.Errorf("could not clear an incomplete private runtime: %w", err)
	}
	if err := os.MkdirAll(runtimeRoot, 0755); err != nil {
		return fmt.Errorf("could not create the private runtime folder: %w", err)
	}
	logPath := filepath.Join(filepath.Dir(runtimeRoot), "python-runtime-install.log")
	args := []string{
		"/quiet", "/log", logPath,
		"InstallAllUsers=0", "Include_launcher=0", "Include_pip=0", "Include_test=0",
		"Include_doc=0", "Include_exe=1", "Include_lib=1", "Include_tcltk=1", "Include_dev=1",
		"Include_tools=0", "AssociateFiles=0", "Shortcuts=0", "PrependPath=0", "AppendPath=0",
		"Include_symbols=0", "Include_debug=0", "TargetDir=" + runtimeRoot,
	}
	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, installer, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("private runtime installation failed: %w (installer log: %s)", err, logPath)
	}
	if err := verifyRuntime(runtimeRoot); err != nil {
		return fmt.Errorf("%w (installer log: %s)", err, logPath)
	}
	return nil
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func appendBootstrapLog(dataRoot, format string, args ...any) {
	_ = os.MkdirAll(dataRoot, 0755)
	path := filepath.Join(dataRoot, "runtime-bootstrap.log")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer file.Close()
	_, _ = fmt.Fprintf(file, "%s "+format+"\n", append([]any{time.Now().Format(time.RFC3339)}, args...)...)
}

func parseRegistryDefaultValue(output string) string {
	for _, line := range strings.Split(output, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		for _, marker := range []string{"REG_SZ", "REG_EXPAND_SZ"} {
			if idx := strings.Index(trimmed, marker); idx >= 0 {
				value := strings.TrimSpace(trimmed[idx+len(marker):])
				if value != "" {
					return os.ExpandEnv(value)
				}
			}
		}
	}
	return ""
}

func registryPythonInstallPaths() []string {
	keys := []string{
		`HKCU\Software\Python\PythonCore\3.12\InstallPath`,
		`HKLM\Software\Python\PythonCore\3.12\InstallPath`,
		`HKLM\Software\WOW6432Node\Python\PythonCore\3.12\InstallPath`,
	}
	var results []string
	for _, key := range keys {
		for _, regView := range []string{"/reg:64", "/reg:32"} {
			cmd := exec.Command("reg.exe", "query", key, "/ve", regView)
			cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
			output, err := cmd.Output()
			if err != nil {
				continue
			}
			if value := parseRegistryDefaultValue(string(output)); value != "" {
				results = append(results, filepath.Clean(value))
			}
		}
	}
	return results
}

func uniquePaths(paths []string) []string {
	seen := map[string]bool{}
	var result []string
	for _, path := range paths {
		if strings.TrimSpace(path) == "" {
			continue
		}
		clean := filepath.Clean(path)
		key := strings.ToLower(clean)
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, clean)
	}
	return result
}

func compatiblePythonCandidates(runtimeRoot string) []string {
	candidates := registryPythonInstallPaths()
	candidates = append(candidates,
		filepath.Join(localData(), "Programs", "Python", "Python312"),
	)
	if programFiles := os.Getenv("ProgramFiles"); programFiles != "" {
		candidates = append(candidates, filepath.Join(programFiles, "Python312"))
	}
	if programFilesX86 := os.Getenv("ProgramFiles(x86)"); programFilesX86 != "" {
		candidates = append(candidates, filepath.Join(programFilesX86, "Python312"))
	}
	var filtered []string
	for _, candidate := range uniquePaths(candidates) {
		if strings.EqualFold(filepath.Clean(candidate), filepath.Clean(runtimeRoot)) {
			continue
		}
		if dirExists(candidate) {
			filtered = append(filtered, candidate)
		}
	}
	return filtered
}

func verifyPythonRoot(root string) error {
	python, err := runtimeInterpreter(root, false)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-c", "import sys, tkinter, ssl, urllib.request; assert sys.version_info[:2] == (3, 12); print('runtime-source-ok')")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if output, err := cmd.CombinedOutput(); err != nil {
		details := strings.TrimSpace(string(output))
		if details == "" {
			details = err.Error()
		}
		return fmt.Errorf("candidate Python runtime is not compatible: %s", details)
	}
	return nil
}

func copyDirectory(source, target string) error {
	if err := os.RemoveAll(target); err != nil {
		return err
	}
	if err := os.MkdirAll(target, 0755); err != nil {
		return err
	}
	return filepath.Walk(source, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		// Never copy user-installed third-party packages into the application's private runtime.
		if info.IsDir() && strings.EqualFold(filepath.Clean(rel), filepath.Join("Lib", "site-packages")) {
			return filepath.SkipDir
		}
		if info.IsDir() && strings.EqualFold(info.Name(), "__pycache__") {
			return filepath.SkipDir
		}
		destination := filepath.Join(target, rel)
		if info.IsDir() {
			return os.MkdirAll(destination, info.Mode())
		}
		input, err := os.Open(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(destination), 0755); err != nil {
			input.Close()
			return err
		}
		output, err := os.OpenFile(destination, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, info.Mode())
		if err != nil {
			input.Close()
			return err
		}
		_, copyErr := io.Copy(output, input)
		inputCloseErr := input.Close()
		outputCloseErr := output.Close()
		if copyErr != nil {
			return copyErr
		}
		if inputCloseErr != nil {
			return inputCloseErr
		}
		return outputCloseErr
	})
}

func cloneCompatibleInstalledRuntime(dataRoot, runtimeRoot string) error {
	candidates := compatiblePythonCandidates(runtimeRoot)
	if len(candidates) == 0 {
		return fmt.Errorf("no compatible local Python 3.12 runtime was found")
	}
	var failures []string
	for _, candidate := range candidates {
		appendBootstrapLog(dataRoot, "checking compatible installed runtime: %s", candidate)
		if err := verifyPythonRoot(candidate); err != nil {
			failures = append(failures, candidate+": "+err.Error())
			continue
		}
		setSetupStatus("Creating the private desktop runtime from the verified local Python 3.12 installation…")
		appendBootstrapLog(dataRoot, "copying verified installed runtime from %s to %s", candidate, runtimeRoot)
		if err := copyDirectory(candidate, runtimeRoot); err != nil {
			failures = append(failures, candidate+": copy failed: "+err.Error())
			continue
		}
		if err := verifyRuntime(runtimeRoot); err != nil {
			_ = os.RemoveAll(runtimeRoot)
			failures = append(failures, candidate+": copied runtime verification failed: "+err.Error())
			continue
		}
		appendBootstrapLog(dataRoot, "private runtime successfully created from %s", candidate)
		return nil
	}
	return fmt.Errorf("compatible installed Python runtime recovery failed: %s", strings.Join(failures, "; "))
}

func runtimeInterpreter(runtimeRoot string, preferWindowed bool) (string, error) {
	python := filepath.Join(runtimeRoot, "python.exe")
	pythonw := filepath.Join(runtimeRoot, "pythonw.exe")
	if preferWindowed && fileExists(pythonw) {
		return pythonw, nil
	}
	if fileExists(python) {
		return python, nil
	}
	if fileExists(pythonw) {
		return pythonw, nil
	}
	return "", fmt.Errorf("private runtime is missing both python.exe and pythonw.exe")
}

func verifyRuntime(runtimeRoot string) error {
	setSetupStatus("Checking the desktop UI runtime…")
	python, err := runtimeInterpreter(runtimeRoot, false)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-c", "import tkinter, ssl, urllib.request; print('runtime-ok')")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if output, err := cmd.CombinedOutput(); err != nil {
		details := strings.TrimSpace(string(output))
		if details == "" {
			details = err.Error()
		}
		return fmt.Errorf("private desktop runtime verification failed: %s", details)
	}
	return nil
}

func prepareRuntime(dataRoot, runtimeRoot string) error {
	appendBootstrapLog(dataRoot, "runtime provisioning started")

	// The CPython full installer enters Modify mode when the same 3.12 installation
	// already exists. In that mode TargetDir can be ignored even though setup exits 0.
	// Prefer cloning a verified compatible local 3.12 installation into our private
	// runtime folder before invoking the installer.
	if err := cloneCompatibleInstalledRuntime(dataRoot, runtimeRoot); err == nil {
		return nil
	} else {
		appendBootstrapLog(dataRoot, "no reusable installed runtime: %v", err)
	}

	downloadDir := filepath.Join(dataRoot, "downloads")
	if err := os.MkdirAll(downloadDir, 0755); err != nil {
		return err
	}
	installer := filepath.Join(downloadDir, installerName)
	if _, err := os.Stat(installer); err != nil {
		setSetupStatus("Connecting to python.org for the signed private runtime…")
		if err := download(installerURL, installer); err != nil {
			return err
		}
	}
	if err := verifySignature(installer); err != nil {
		_ = os.Remove(installer)
		setSetupStatus("The cached runtime was invalid. Downloading a clean copy…")
		if downloadErr := download(installerURL, installer); downloadErr != nil {
			return downloadErr
		}
		if verifyErr := verifySignature(installer); verifyErr != nil {
			_ = os.Remove(installer)
			return verifyErr
		}
	}

	installErr := installRuntime(installer, runtimeRoot)
	if installErr == nil {
		appendBootstrapLog(dataRoot, "private runtime installed directly into %s", runtimeRoot)
		return nil
	}
	appendBootstrapLog(dataRoot, "direct private runtime install failed: %v", installErr)

	// A successful CPython Modify operation may have repaired/updated the registered
	// installation rather than populated TargetDir. Re-check and clone it now.
	if recoveryErr := cloneCompatibleInstalledRuntime(dataRoot, runtimeRoot); recoveryErr == nil {
		return nil
	} else {
		appendBootstrapLog(dataRoot, "post-installer runtime recovery failed: %v", recoveryErr)
		return fmt.Errorf("%v; automatic private-runtime recovery also failed: %v", installErr, recoveryErr)
	}
}

func launchManager(dir, runtimeRoot string) {
	appScript := filepath.Join(dir, "DragonwildsServerManager.pyw")
	if _, err := os.Stat(appScript); err != nil {
		fail(fmt.Errorf("application files are incomplete: %s is missing", appScript))
	}
	selfTest := len(os.Args) > 1 && os.Args[1] == "--self-test"
	runner, err := runtimeInterpreter(runtimeRoot, !selfTest)
	if err != nil {
		fail(err)
	}
	args := []string{appScript}
	for _, arg := range os.Args[1:] {
		args = append(args, arg)
	}
	cmd := exec.Command(runner, args...)
	cmd.Dir = dir
	launcherExe, _ := os.Executable()
	cmd.Env = append(os.Environ(), "PYTHONUTF8=1", "PYTHONPATH="+dir, "DWSM_LAUNCHER_EXE="+launcherExe)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if !selfTest {
		if err := cmd.Start(); err != nil {
			fail(fmt.Errorf("could not start the manager: %w", err))
		}
		return
	}
	reportDir := filepath.Join(localData(), appName)
	_ = os.MkdirAll(reportDir, 0755)
	reportPath := filepath.Join(reportDir, "SELF_TEST_RESULTS.txt")
	report, err := os.Create(reportPath)
	if err != nil {
		fail(err)
	}
	cmd.Stdout = report
	cmd.Stderr = report
	runErr := cmd.Run()
	_ = report.Close()
	if runErr != nil {
		messageBox("Dragonwilds Server Manager", "Self-test failed. Results were written to:\n"+reportPath, 0x10)
		os.Exit(1)
	}
	messageBox("Dragonwilds Server Manager", "Self-test passed. Results were written to:\n"+reportPath, 0x40)
}

func main() {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	dir, err := exeDir()
	if err != nil {
		fail(err)
	}
	dataRoot := filepath.Join(localData(), appName)
	runtimeRoot := filepath.Join(dataRoot, "Runtime")
	if err := verifyRuntime(runtimeRoot); err != nil {
		setupStatus = "Preparing the private desktop runtime…"
		if setupErr := showSetupWindow(func() error { return prepareRuntime(dataRoot, runtimeRoot) }); setupErr != nil {
			fail(setupErr)
		}
	}
	if len(os.Args) > 1 && os.Args[1] == "--provision-runtime-only" {
		return
	}
	launchManager(dir, runtimeRoot)
}
