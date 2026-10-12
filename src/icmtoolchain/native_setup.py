import os
import platform
import re
import zipfile
from os import environ, getenv, makedirs
from os.path import basename, exists, isdir, isfile, join
from typing import Any, Generator, List, Optional, Union

from .context import GLOBALS
from .errors import abort
from .fetch import queue_download_request
from .logger import attention, failure, print, success, warn
from .shell import InteractiveSession, Progress, confirm_prompt
from .utils import (AttributeZipFile, RuntimeCodeError, ensure_not_whitespace,
                    get_os_bitness, iterate_subdirectories,
                    read_properties_stream, remove_tree)

ABIS = {
	"armeabi-v7a": "arm",
	"arm64-v8a": "arm64",
	"x86": "x86",
	"x86_64": "x86_64"
}
ABI_TARGETS = {
	"armeabi-v7a": "armv7a-linux-androideabi21",
	"arm64-v8a": "aarch64-linux-android24",
	"x86": "i686-linux-android21",
	"x86_64": "x86_64-linux-android24",
}
FALLBACK_NDK_VERSIONS = {
	"armeabi-v7a": "21.4.7075529",
	"arm64-v8a": "21.4.7075529"
}
FALLBACK_CPP_STANDARD = "-std=c++17"

GCC_EXECUTABLES = dict()

def abi_to_architecture(abi: str) -> str:
	if abi in ABIS:
		return ABIS[abi]
	raise ValueError(f"Unsupported ABI {abi!r}!")

def abi_to_ndk_target(abi: str) -> str:
	if abi in ABI_TARGETS:
		return ABI_TARGETS[abi]
	raise ValueError(f"Unsupported ABI {abi!r}!")

def ndk_version_to_revision(version: str) -> str:
	try:
		try:
			major, minor, patch = version.split(".", 3)
		except ValueError:
			major, minor = version.split(".", 2)
	except ValueError:
		major = version
		minor = "0"
	try:
		major = int(major)
		minor = int(minor)
	except ValueError as exc:
		raise RuntimeCodeError(1, "Invalid NDK Version: " + version + " (" + str(exc) + ")")
	suffix = "" if minor <= 0 else chr(97 + minor)
	return "r" + str(major) + suffix

def search_ndk_subdirectories(directory: str) -> Generator[str, Any, Any]:
	preferred_directory_regexes = [
		r"android-ndk-r\d+\b",
		r"android-ndk-.*",
		r"ndk[/\\]+\d+(\.\d+)+",
		"ndk-bundle"
	]
	for directory_regex in preferred_directory_regexes:
		compiled_pattern = re.compile(directory_regex + "$")
		for subdirectory in iterate_subdirectories(directory):
			if re.search(compiled_pattern, subdirectory):
				yield subdirectory

def search_unversioned_ndk_path(home_directory: str, contains_ndk: bool = False) -> Generator[str, Any, Any]:
	if contains_ndk:
		for ndk in search_ndk_subdirectories(home_directory):
			yield ndk
	root_ndk_path = environ.get("NDK_PATH")
	if root_ndk_path:
		yield root_ndk_path
	try:
		android_tools = environ["ANDROID_SDK_ROOT"]
	except KeyError:
		android_tools = join(home_directory, "Android")
	if exists(android_tools):
		for ndk in search_ndk_subdirectories(android_tools):
			yield ndk
	android_tools = join(android_tools, "ndk")
	if exists(android_tools):
		for ndk in search_ndk_subdirectories(android_tools):
			yield ndk

def search_ndk_path(home_directory: str, contains_ndk: bool = False, ndk_version: Optional[str] = None) -> Optional[str]:
	for ndk_path in search_unversioned_ndk_path(home_directory, contains_ndk):
		if not ndk_version:
			return ndk_path
		package_version = read_ndk_source_version(ndk_path)
		if package_version:
			if "." not in ndk_version:
				package_version = package_version.split(".", 1)[0]
			if ndk_version == package_version:
				return ndk_path

def read_ndk_source_version(ndk_path: str) -> Optional[str]:
	properties_path = join(ndk_path, "source.properties")
	if not isfile(properties_path):
		return
	with open(properties_path, encoding="utf-8") as properties_file:
		properties = read_properties_stream(properties_file)
	try:
		return properties["Pkg.Revision"]
	except KeyError:
		pass

def get_ndk_path(ndk_version: Optional[str] = None) -> Optional[str]:
	relative_path = GLOBALS.TOOLCHAIN_CONFIG.get_value("tools.ndk", GLOBALS.TOOLCHAIN_CONFIG.get_value("native.ndkPath", GLOBALS.TOOLCHAIN_CONFIG.get_value("ndkPath")))
	if ensure_not_whitespace(relative_path):
		ndk_path = GLOBALS.TOOLCHAIN_CONFIG.get_path(relative_path)
		if not isdir(ndk_path):
			from .output_directory import get_config_directory
			ndk_path = join(get_config_directory(), relative_path)
		if isdir(ndk_path):
			return ndk_path
	# TODO: Rewrite to output_directory strategy...
	try:
		# Unix
		return search_ndk_path(environ["HOME"], ndk_version=ndk_version)
	except KeyError:
		pass
	# Windows
	return search_ndk_path(getenv("LOCALAPPDATA", "."), ndk_version=ndk_version)

def get_ndk_version_for_abi(abi: str) -> Optional[str]:
	ndk_version = GLOBALS.PREFERRED_CONFIG.get_value("native.ndkVersions." + abi)
	if not ndk_version and abi in FALLBACK_NDK_VERSIONS:
		ndk_version = FALLBACK_NDK_VERSIONS[abi]
	return ndk_version

def search_for_clang_executable(ndk_directory: str, verbose: bool = False) -> Optional[str]:
	system = platform.system()
	machine = platform.machine().lower()
	is_android = "TERMUX_VERSION" in os.environ or "ANDROID_ROOT" in os.environ
	is_arm_host = machine in ("aarch64", "arm64", "armv8l")

	if is_android or (system == "Linux" and is_arm_host):
		import shutil
		clang_path = shutil.which("clang++")
		if clang_path:
			return clang_path

		if verbose:
			if is_android:
				attention(
					"Clang compiler is required to build native modules on Android, but it is not installed.",
					"You can install it using Termux package manager:"
				)
				warn(" - pkg update")
				warn(" - pkg install clang")
			else:
				attention(
					f"Official Android NDK does not provide prebuilt binaries for {system} ({machine}) host.",
					"You need to install system Clang to cross-compile modules:"
				)
				warn(" - sudo apt-get install clang")
				warn(" - sudo pacman -S clang")
				warn(" - sudo dnf install clang")
			warn("Make sure 'clang++' is accessible in your PATH environment variable.")
		return

	prebuilt_dir = os.path.join(ndk_directory, "toolchains", "llvm", "prebuilt")
	if os.path.isdir(prebuilt_dir):
		subdirs = [entry.name for entry in os.scandir(prebuilt_dir) if entry.is_dir()]
		if subdirs:
			host_tag = subdirs[0]
			exe_name = "clang++.exe" if system == "Windows" else "clang++"
			candidate = os.path.join(prebuilt_dir, host_tag, "bin", exe_name)
			if os.path.isfile(candidate):
				return candidate

	import shutil
	system_clang = shutil.which("clang++")
	if system_clang:
		return system_clang

	if verbose:
		attention(
			f"Could not locate Clang compiler executable inside Android NDK at '{ndk_directory}'.",
			"We were unable to detect valid toolchain binaries. You can try following options:"
		)
		if system == "Windows":
			if is_arm_host:
				warn(" - If you are on Windows on ARM, install LLVM: winget install LLVM.LLVM")
			warn(" - Verify that NDK archive was extracted completely without file corruptions.")
			warn(" - Ensure path to NDK does not contain special characters or exceed MAX_PATH.")
		elif system == "Darwin":
			warn(" - Reinstall or verify Android NDK r21e for macOS.")
			warn(" - Alternatively, install LLVM via Homebrew: brew install llvm")
		else:
			warn(" - Ensure read and execute permissions (+x) are granted for binaries in NDK directory.")
			warn(" - sudo apt-get install clang (or install LLVM package via your package manager)")
		warn(f"Expected toolchain location: {os.path.join(prebuilt_dir, '<host>', 'bin')}")

def require_compiler_executable(abi: str, install_if_required: bool = False) -> Optional[str]:
	from .output_directory import get_config_directory
	ndk_directory = join(get_config_directory(), "ndk", abi)
	file = search_for_clang_executable(ndk_directory)

	if not file or not isfile(file):
		ndk_version = get_ndk_version_for_abi(abi)
		ndk_path = get_ndk_path(ndk_version=ndk_version)
		if ndk_path:
			file = search_for_clang_executable(ndk_path, verbose=True)

	if install_if_required and (not file or not isfile(file)):
		attention(f"Executable of GCC ({abi}) is not found, trying to reinstall it.")
		if install_ndk(abis=abi, reinstall=True) != 0:
			return
		file = search_for_clang_executable(ndk_directory)
		if not file or not isfile(file):
			return

	return file

def prepare_compiler_executable(abi: str) -> str:
	if abi in GCC_EXECUTABLES:
		return GCC_EXECUTABLES[abi]
	executable = require_compiler_executable(abi, install_if_required=True)
	if not executable:
		from .native_build import CODE_FAILED_NO_GCC
		raise RuntimeCodeError(CODE_FAILED_NO_GCC, f"Failed to acquire compiler executable from NDK for ABI {abi!r}!")
	GCC_EXECUTABLES[abi] = executable
	return executable

def check_installation(abis: Union[str, List[str]]) -> bool:
	if not isinstance(abis, list):
		abis = [abis]
	for abi in abis:
		if not require_compiler_executable(abi, install_if_required=False):
			return False
	return True

def get_download_ndk_url(revision: str) -> str:
	overriden_url = GLOBALS.TOOLCHAIN_CONFIG.get_value("native.ndkUrl", GLOBALS.TOOLCHAIN_CONFIG.get_value("ndkUrl"))
	if overriden_url and isinstance(overriden_url, str):
		return overriden_url
	pattern_version = re.search(r"\d+", revision)
	requires_architecture = pattern_version and int(pattern_version[0]) >= 11 and int(pattern_version[0]) <= 22
	is_32bit = get_os_bitness() == 32
	if is_32bit and (not pattern_version or requires_architecture or int(pattern_version[0]) >= 18):
		raise RuntimeCodeError(255, "Your platform is not supported anymore, you should upgrade to 64 bit for using Android NDK r18 and newer.")
	package_suffix = platform.system()
	if package_suffix == "Windows":
		if is_32bit or requires_architecture:
			package_suffix = "windows-x86" if is_32bit else "windows-x86_64"
		package_suffix = package_suffix or "windows"
	# TODO: Maybe also extract dmg archives from MacOS?
	elif package_suffix == "Darwin":
		package_suffix = "darwin-x86_64" if requires_architecture else "darwin"
	else:
		if is_32bit:
			raise RuntimeCodeError(255, f"Your platform {package_suffix} should be upgraded to 64 bit, otherwise Android NDK cannot be installed.")
		if package_suffix != "Linux":
			attention(f"Expected platform Windows, MacOS or Linux. Got: {package_suffix}, falling back to Linux.")
		package_suffix = "linux-x86_64" if requires_architecture else "linux"
	return f"https://dl.google.com/android/repository/android-ndk-{revision}-{package_suffix}.zip"

def download_ndk(ndk_version: Optional[str] = None) -> Optional[str]:
	revision = ndk_version_to_revision(ndk_version) if ndk_version else "r21e"

	archive_path = queue_download_request(get_download_ndk_url(revision))
	if archive_path is None:
		return None

	with InteractiveSession(progress=Progress("Extracting NDK/GCC")) as session:
		try:
			from os import environ
			from os.path import expanduser
			home_directory = expanduser("~")
			android_tools = environ.get("ANDROID_SDK_ROOT") or environ.get("ANDROID_HOME") or join(home_directory, "Android", "Sdk")
			extract_path = join(android_tools, "ndk")
		except:
			from .output_directory import get_temporary_directory
			extract_path = join(get_temporary_directory(), f"ndk")
		
		makedirs(extract_path, exist_ok=True)
		try:
			with AttributeZipFile(archive_path, "r") as archive:
				archive.extractall(extract_path)
			success(f"Extracted NDK/GCC into {extract_path}.")
		except OSError as exc:
			failure(f"#{exc.errno}: {basename(exc.filename)}")
			try:
				remove_tree(extract_path)
			except OSError:
				failure(f"#{exc.errno}: {basename(exc.filename)} (security fail)")
		except zipfile.BadZipFile as exc:
			try:
				remove_tree(extract_path)
				return download_ndk(revision)
			except OSError as exc:
				failure(f"#{exc.errno}: {basename(exc.filename)} (security fail)")

	return search_ndk_path(extract_path, contains_ndk=True, ndk_version=ndk_version)

def install_ndk_for_architecture(abi: str, reinstall: bool = False) -> int:
	ndk_version = get_ndk_version_for_abi(abi)
	ndk_path = get_ndk_path(ndk_version=ndk_version)

	if not ndk_path:
		if not reinstall:
			print(f"Not found valid NDK installation for {abi}.")
		question = "Install NDK from Android Repository?"
		if ndk_version:
			question = f"Install NDK {ndk_version} from Android Repository?"
		if reinstall or confirm_prompt(question, True):
			ndk_path = download_ndk(ndk_version=ndk_version)
		else:
			abort()

	if not ndk_path:
		failure("Installation interrupted by raised cause above, you are must extract 'temp/ndk-r**.zip' manually into temp and retry task.")
		return 1
	return 0

def install_ndk(abis: Union[str, List[str]] = "arm", reinstall: bool = False) -> int:
	result = 0
	if not isinstance(abis, list):
		abis = [abis]
	for abi in abis:
		result = install_ndk_for_architecture(abi, reinstall=reinstall)
		if result != 0:
			break

	try:
		if result == 0:
			from .output_directory import get_temporary_directory
			remove_tree(join(get_temporary_directory(), "network"))
	except OSError as exc:
		pass

	if result != 0:
		warn("Please use a different version of Android NDK or report this issue to developer.")
	return result
