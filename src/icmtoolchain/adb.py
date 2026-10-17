import platform
import re
import subprocess
from os.path import join
from typing import Any, Dict, List, Optional, Tuple

from .context import GLOBALS
from .fetch import queue_download_request
from .logger import attention, failure, success
from .output_directory import get_config_directory, get_temporary_directory
from .shell import confirm_prompt
from .utils import DEVNULL, AttributeZipFile, remove_tree

LAUNCHER_PACKAGES = [
	"com.zheka.horizon64",
	"com.zheka.horizon32",
	"com.zheka.horizon",
	"com.zhekasmirnov.innercore"
]

STATE_UNKNOWN = -1
STATE_DEVICE_CONNECTED = 0
STATE_NO_DEVICES = 1
STATE_DISCONNECTED = 2
STATE_DEVICE_AUTHORIZING = 3

def download_adb() -> str:
	system = platform.system().lower()
	if system == "windows":
		url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
	elif system == "darwin":
		url = "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"
	else:
		url = "https://dl.google.com/android/repository/platform-tools-latest-linux.zip"

	archive_path = join(get_temporary_directory(), "platform-tools.zip")
	should_ok = queue_download_request(url, output_path=archive_path)
	if not should_ok:
		raise RuntimeError("ADB cannot be installed or being cancelled.")

	adb_dir = join(get_config_directory(), "adb")
	with AttributeZipFile(archive_path, "r") as archive:
		archive.extractall(get_temporary_directory())

	import shutil
	extracted_dir = join(get_temporary_directory(), "platform-tools")
	remove_tree(adb_dir)
	shutil.move(extracted_dir, adb_dir)
	remove_tree(archive_path)

	success("Successfully downloaded and installed ADB (platform-tools).")

	if system == "windows":
		return join(adb_dir, "adb.exe")
	return join(adb_dir, "adb")

def get_adb_executable(install_allowed: bool = True) -> str:
	custom_path = GLOBALS.TOOLCHAIN_CONFIG.get_value("tools.adb", GLOBALS.TOOLCHAIN_CONFIG.get_value("adb.path"))
	if custom_path:
		from os.path import isfile
		if isfile(custom_path):
			return custom_path
	try:
		import shutil
		if shutil.which("adb"):
			return "adb"
	except:
		pass

	if platform.system() == "Windows":
		adb_executable = join(get_config_directory(), "adb", "adb.exe")
	else:
		adb_executable = join(get_config_directory(), "adb", "adb")
	if not isfile(adb_executable):
		if install_allowed:
			return download_adb()
		from .errors import abort
		abort("Component 'adb' is required for pushing, nothing to do.")
	return adb_executable

def ensure_server_running(retry: int = 0) -> bool:
	try:
		subprocess.run([
			get_adb_executable(),
			"start-server"
		], check=True, stdout=DEVNULL, stderr=DEVNULL)
		return True
	except subprocess.CalledProcessError as err:
		if retry >= 3:
			failure("adb start-server failed with code", err.returncode)
			return False
		return ensure_server_running(retry + 1)

def which_state(what: Optional[str] = None) -> int:
	if what is None:
		return STATE_UNKNOWN
	try:
		return {
			"no devices": STATE_NO_DEVICES,
			"device": STATE_DEVICE_CONNECTED,
			"authorizing": STATE_DEVICE_AUTHORIZING,
			"unauthorized": STATE_DEVICE_AUTHORIZING
		}[what]
	except KeyError: # offline
		return STATE_DISCONNECTED

def get_device_state() -> int:
	try:
		pipe = subprocess.run([
			get_adb_executable(),
			"get-state"
		], text=True, timeout=3.0, check=True, capture_output=True)
	except subprocess.CalledProcessError as err:
		if err.returncode == 1:
			return STATE_NO_DEVICES
		failure("adb get-state failed with code", err.returncode)
		return STATE_UNKNOWN
	except subprocess.TimeoutExpired:
		return STATE_UNKNOWN
	return which_state(pipe.stdout.strip())

def get_device_serial() -> Optional[str]:
	try:
		pipe = subprocess.run([
			get_adb_executable(),
			"get-serialno"
		], text=True, check=True, capture_output=True)
	except subprocess.CalledProcessError as err:
		attention("adb get-serialno failed with code", err.returncode)
		return None
	return pipe.stdout.strip()

def device_list() -> Optional[List[Dict[str, Any]]]:
	try:
		pipe = subprocess.run([
			get_adb_executable(),
			"devices", "-l"
		], text=True, check=True, capture_output=True)
	except subprocess.CalledProcessError as err:
		attention("adb devices failed with code", err.returncode)
		return None
	data = pipe.stdout.rstrip().splitlines()
	data.pop(0)
	devices = []
	for device in data:
		parts = re.split(r"\s+", device)
		devices.append({
			"serial": parts[0],
			"state": which_state(parts[1]),
			"data": parts[2:]
		})
	return devices

def wait_for_authorization(serial: Optional[str] = None, timeout: float = 15.0) -> bool:
	from time import sleep, time

	from .logger import attention, success
	
	start_time = time()
	notified = False
	
	while time() - start_time < timeout:
		current_state = STATE_UNKNOWN
		if serial:
			devices = device_list()
			if devices:
				for device in devices:
					if device["serial"] == serial:
						current_state = device["state"]
						break
		else:
			current_state = get_device_state()

		if current_state == STATE_DEVICE_CONNECTED:
			if notified:
				success("Device authorized successfully!")
			return True
		elif current_state == STATE_DEVICE_AUTHORIZING:
			if not notified:
				attention("Device is unauthorized. Please confirm USB debugging on your device screen...")
				notified = True

		sleep(1.0)

	if notified:
		attention("Authorization timeout. Device is still unauthorized.")
	return False

def ensure_device_ready(timeout: float = 15.0) -> bool:
	ensure_server_running()
	state = get_device_state()
	if state == STATE_DEVICE_CONNECTED:
		return True
	if state in (STATE_DEVICE_AUTHORIZING, STATE_NO_DEVICES, STATE_DISCONNECTED, STATE_UNKNOWN):
		return wait_for_authorization(timeout=timeout)
	return False

def get_adb_command_by_serial(serial: str) -> List[str]:
	ensure_server_running()
	devices = GLOBALS.TOOLCHAIN_CONFIG.obtain_list("devices")
	if not serial in devices:
		try:
			from ipaddress import ip_address
			ip_address(serial.partition(":")[0])
		except ValueError:
			devices.append(serial)
			GLOBALS.TOOLCHAIN_CONFIG.set_value("devices", devices)
			GLOBALS.TOOLCHAIN_CONFIG.save_as_file()
	return [
		get_adb_executable(),
		"-s", serial
	]

def get_adb_command_by_tcp(ip: str, port: Optional[int] = None, skip_error: bool = False) -> Optional[List[str]]:
	ensure_server_running()
	if not get_adb_command_by_serialno_type("-e", silent=skip_error):
		if skip_error or not confirm_prompt("Are you really want to save it?", False):
			return None
	device: dict[str, Any] = {
		"ip": ip
	}
	if port:
		device["port"] = port
	devices = GLOBALS.TOOLCHAIN_CONFIG.obtain_list("devices")
	if not device in devices:
		devices.append(device)
		GLOBALS.TOOLCHAIN_CONFIG.set_value("devices", devices)
		GLOBALS.TOOLCHAIN_CONFIG.save_as_file()
	return [
		get_adb_executable(),
		"-e"
	]

def get_adb_command_by_serialno_type(which: str, silent: bool = False) -> Optional[List[str]]:
	serial = subprocess.run([
		get_adb_executable(),
		which, "get-serialno"
	], text=True, capture_output=True)
	if serial.returncode != 0:
		if not silent:
			attention("adb get-serialno failed with code", serial.returncode)
		return None
	return get_adb_command_by_serial(serial.stdout.rstrip())

def launch_package_via_am(package: str, activity: str) -> bool:
	try:
		process = subprocess.run(GLOBALS.ADB_COMMAND + [
			"shell", "am", "start",
			"-n", f"{package}/{activity}",
			"--ez", "autoLaunchFlag", "true"
		], check=True, capture_output=True, text=True)

		output = process.stdout + process.stderr
		if "does not exist" in output:
			return False
		elif "Error" in output:
			attention(f"Unexpected error while launching {package}:\n{output.strip()}")
			return False
		return True
	except subprocess.CalledProcessError as err:
		output = err.stdout + err.stderr
		if "does not exist" not in output:
			attention(f"Failed to launch {package} (am start) with code {err.returncode}:\n{output.strip()}")
		return False

def launch_package_via_monkey(package: str) -> bool:
	try:
		process = subprocess.run(GLOBALS.ADB_COMMAND + [
			"shell", "monkey",
			"-p", package,
			"-c", "android.intent.category.LAUNCHER", "1"
		], check=True, capture_output=True, text=True)

		output = process.stdout + process.stderr
		if "No activities found to run" in output:
			return False
		elif "Events injected" in output:
			return True
		else:
			attention(f"Unexpected output from monkey for {package}:\n{output.strip()}")
			return False
	except subprocess.CalledProcessError as err:
		output = err.stdout + err.stderr
		if "No activities found to run" not in output:
			attention(f"Failed to launch {package} (monkey) with code {err.returncode}:\n{output.strip()}")
		return False

def test_directory_exist(path: str, *args: str) -> bool:
	escaped_path = "'" + path.replace("'", "'\\''") + "'"
	try:
		subprocess.run(GLOBALS.ADB_COMMAND + [
			"shell", "test", "-d", escaped_path
		] + list(args), check=True)
	except subprocess.CalledProcessError as err:
		if err.returncode != 1:
			failure("adb shell test -d failed with code", err.returncode)
		return False
	return True

def ls(path: str, show_hidden: bool = True) -> Tuple[List[str], List[str]]:
	escaped_path = "'" + path.replace("'", "'\\''") + "'"
	glob_pattern = ".* *" if show_hidden else "*"

	list_exec = (
		f"cd {escaped_path} >/dev/null 2>&1 || exit 1\n"
		f"for f in {glob_pattern}; do\n"
		f"[ \"$f\" = \".\" ] || [ \"$f\" = \"..\" ] && continue\n"
		f"[ -e \"./$f\" ] || [ -L \"./$f\" ] || continue\n"
		f"if [ -d \"./$f\" ]; then echo \"$f/\"; else echo \"$f\"; fi\n"
		f"done\n"
		f"exit 0\n"
	)

	try:
		pipe = subprocess.run(
			GLOBALS.ADB_COMMAND + ["shell"],
			input=list_exec, text=True, encoding="utf-8", errors="replace", check=True, capture_output=True
		)
	except subprocess.CalledProcessError as err:
		if err.returncode != 1:
			failure("adb shell ls failed with code", err.returncode)
		return [], []
	except KeyboardInterrupt:
		return [], []

	files, directories = [], []
	
	for line in pipe.stdout.splitlines():
		if not line:
			continue
		if line.endswith("/"):
			directories.append(line[:-1])
		else:
			files.append(line)

	return directories, files
