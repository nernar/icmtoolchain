import platform
import shutil
import subprocess
from json import loads as json_loads
from os import mkdir
from os.path import exists, isfile, join
from typing import List, Optional, Tuple
from urllib.request import urlopen

from .context import GLOBALS
from .errors import abort
from .fetch import queue_download_request, retrieve_bytes
from .logger import debug, failure, info, success, warn
from .output_directory import get_config_directory, get_temporary_directory
from .shell import InteractiveSession, Progress, confirm_prompt
from .utils import (AttributeZipFile, remove_tree, request_executable_version,
                    request_tool)


def get_node_packages_directory() -> str:
	packages_directory_override = GLOBALS.TOOLCHAIN_CONFIG.get_value("tools.packages")
	from .output_directory import get_config_directory
	return packages_directory_override or join(get_config_directory(), "packages")

def download_node(lts: bool = False) -> str:
	debug("Fetching Node.js versions...")
	version_index = json_loads(retrieve_bytes("https://nodejs.org/dist/index.json"))
	if not isinstance(version_index, list):
		raise RuntimeError("Malformed Node.js dist/index.json file!")
	latest_version = None
	for version in version_index:
		if lts and "lts" not in version or not version["lts"]:
			continue
		latest_version = version["version"]
		break
	if not latest_version:
		raise RuntimeError(f"No suitable version in {len(version_index)} version(s) could be found!")

	system = platform.system().lower()
	if system == "windows":
		url = f"https://nodejs.org/dist/{latest_version}/node-{latest_version}-win-x64.zip"
	elif system == "darwin":
		url = f"https://nodejs.org/dist/{latest_version}/node-{latest_version}-darwin-x64.tar.gz"
	else:
		url = f"https://nodejs.org/dist/{latest_version}/node-{latest_version}-linux-x64.tar.gz"
	debug(f"Fetching Node.js {latest_version} from {url}...")

	archive_path = queue_download_request(url)
	if not archive_path:
		abort("Node.js cannot be installed or being cancelled.")
	node_directory_override = GLOBALS.TOOLCHAIN_CONFIG.get_value("tools.node")
	from .output_directory import get_config_directory
	node_directory = node_directory_override or join(get_config_directory(), "node")

	with InteractiveSession(progress=Progress("Extracting Node.js...")):
		if archive_path.endswith(".zip"):
			with AttributeZipFile(archive_path, "r") as archive:
				archive.extractall(get_temporary_directory())
		else:
			import tarfile
			with tarfile.open(archive_path, "r:gz") as archive:
				archive.extractall(get_temporary_directory())

	import os
	extracted_folders = [f for f in os.listdir(get_temporary_directory()) if f.startswith("node-v20")]
	if not extracted_folders:
		raise RuntimeError("Failed to extract Node.js")

	extracted_dir = join(get_temporary_directory(), extracted_folders[0])
	remove_tree(node_directory)
	shutil.move(extracted_dir, node_directory)
	remove_tree(archive_path)

	success("Successfully downloaded and installed Node.js.")
	return node_directory

def install_typescript(version: Optional[str] = "5", globally: bool = False):
	if not confirm_prompt(f"Do you want to {'globally' if globally else 'locally'} install TypeScript Compiler{' v' + version if version else ''} (recommended)?", True):
		return None

	if globally:
		info(f"Installing TypeScript Compiler globally via npm...")
		subprocess.run(f"npm install -g typescript{'@' + version if version else ''}", shell=platform.system() == "Windows")
		tsc = request_tool("tsc") or shutil.which("tsc")
		if tsc:
			return tsc

	else:
		info(f"Installing TypeScript Compiler locally via npm...")
		node_directory = get_node_packages_directory()
		subprocess.run("npm install typescript{'@' + version if version else ''}", cwd=node_directory, shell=platform.system() == "Windows")
		node_bin_directory = join(node_directory, "node_modules", ".bin")
		tsc = shutil.which("tsc", path=node_bin_directory)
		if tsc:
			GLOBALS.TOOLCHAIN_CONFIG.set_value("tools.tsc", tsc)
			GLOBALS.TOOLCHAIN_CONFIG.save_as_file()
			return tsc

checked_tsc_requirements = False

def check_tsc_requirements(tsc: str) -> bool:
	global checked_tsc_requirements
	if checked_tsc_requirements:
		return True
	version = request_executable_version(tsc)
	if version < 3.0:
		failure("TypeScript Compiler 3.0 or above required to transpile mods via Inner Core Mod Toolchain.")
	elif version >= 7:
		failure("TypeScript Compiler >= 7.0 cannot be used to transpile mods via Inner Core Mod Toolchain.")
	else:
		checked_tsc_requirements = True
		return True
	return False

def request_typescript(only_check: bool = False) -> Optional[str]:
	"""
	Utility to check and install tsc with npm.
	"""
	if GLOBALS.TOOLCHAIN_CONFIG.get_value("denyTypeScript"):
		return None

	tsc = request_tool("tsc") or shutil.which("tsc")
	if not tsc:
		node_bin_directory = join(get_node_packages_directory(), "node_modules", ".bin")
		tsc = shutil.which("tsc", path=node_bin_directory)
	if tsc or only_check:
		return tsc
	elif tsc and check_tsc_requirements(tsc):
		return tsc

	if not confirm_prompt("Do you want to enable TypeScript and ES6+ support (requires Node.js to build project)?", True):
		return None

	npm = shutil.which("npm")
	if not npm:
		debug("Node.js not found in system, downloading local instance...")
		download_node()
		return request_typescript(only_check=False)

	tsc = install_typescript(globally=not tsc)
	if tsc:
		return tsc
	failure("Something went wrong when trying to install TypeScript Compiler, please check your Node.js and npm installation and try again.")
	return None

def request_typescript_flags(tsc: str) -> List[str]:
	tsc_version = request_executable_version(tsc)
	if 6.0 <= tsc_version < 7.0:
		return ["--ignoreDeprecations", "6.0"]
	return []

def fetch_declarations() -> bool:
	try:
		response = urlopen("https://nernar.github.io/declarations/core-engine.d.ts")
		declaration = response.read().decode("utf-8")
	except Exception as e:
		warn(f"Failed to fetch declarations: {e}")
		return False

	declaration_path = join(get_config_directory(), "declarations")
	if not exists(declaration_path):
		mkdir(declaration_path)
	with open(join(declaration_path, "core-engine.d.ts"), "w", encoding="utf-8") as file:
		file.write(declaration)
	commit_path = join(declaration_path, ".commit")
	if not isfile(commit_path):
		open(commit_path, "w").close()

	success("Installed latest version of core-engine.d.ts declarations.")
	return True

def get_tsc_version() -> Tuple[int, ...]:
	tsc = request_typescript(only_check=True)
	if not tsc:
		return (0, 0, 0)
	try:
		output = subprocess.check_output(
			[tsc, "--version"],
			shell=platform.system() == "Windows",
			stderr=subprocess.DEVNULL,
			text=True,
		).strip()
		parts = output.split()
		if len(parts) >= 2:
			return tuple(int(x) for x in parts[-1].split("."))
	except (subprocess.SubprocessError, ValueError, OSError):
		pass
	return (0, 0, 0)

def should_use_babel() -> bool:
	from .context import GLOBALS
	babel_setting = GLOBALS.PREFERRED_CONFIG.get_value("babel", "auto")

	if babel_setting is True:
		return True
	if babel_setting is False:
		return False

	try:
		from .babel_setup import has_project_babel_config, request_babel
		if has_project_babel_config(GLOBALS.MAKE_CONFIG.directory):
			return True
		if not request_babel(only_check=True):
			return False
	except Exception:
		return False

	version = get_tsc_version()
	if version >= (6, 0, 0):
		return True

	return False
