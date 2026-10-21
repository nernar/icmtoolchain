import json
import os
import platform
import re
import shutil
import subprocess
from os.path import abspath, exists, isdir, isfile, islink, join, relpath
from typing import (Any, Callable, Dict, Iterable, List, Optional, TextIO,
                    Tuple, Union, overload)
from zipfile import ZipFile, ZipInfo

from .logger import warn

DEVNULL = open(os.devnull, "w")


class RuntimeCodeError(RuntimeError):
	code: int

	def __init__(self, code: int, *args: object):
		self.code = code
		RuntimeError.__init__(self, *args)

def move_to_backup(path: str) -> None:
	if not isfile(path) and not isdir(path) and not islink(path):
		return
	output_path = path + ".bak"
	if exists(output_path):
		remove_tree(output_path)
	os.rename(path, output_path)

def ensure_directory(directory: str) -> None:
	"""
	Ensures that specified path is directory,
	removes existing file if it exists.
	"""
	if isfile(directory) or islink(directory):
		move_to_backup(directory)
	if not exists(directory):
		os.makedirs(directory, exist_ok=True)

def ensure_file_directory(file: str) -> None:
	"""
	Ensures that specified file parent directory is exists,
	removes existing file if it exists.
	"""
	ensure_directory(abspath(join(file, "..")))

def ensure_file(path: str) -> None:
	ensure_file_directory(path)
	if isdir(path) or islink(path):
		move_to_backup(path)

def flush_json_if_changed(json_path: str, contents: Any):
	if os.path.isfile(json_path):
		previous_contents = None
		with open(json_path, "r", encoding="utf-8") as json_file:
			try:
				previous_contents = json.load(json_file)
			except json.JSONDecodeError as exc:
				warn(f"Malformed json {json_path!r}: {exc.msg} (#{exc.lineno}:{exc.colno})")
		if contents == previous_contents:
			return
	ensure_file(json_path)
	with open(json_path, "w", encoding="utf-8") as file:
		file.write(json.dumps(contents, indent="\t", ensure_ascii=False) + "\n")

def remove_tree(directory: str) -> bool:
	"""
	Removes existing files, directories and links recursive.
	"""
	if not exists(directory):
		return False
	if isfile(directory) or islink(directory):
		os.remove(directory)
		return True
	try:
		shutil.rmtree(directory, ignore_errors=False)
	except Exception:
		return False
	return True

def copy_file(source: str, destination: str) -> None:
	"""
	Copies source file to destination file or directory,
	non-existing parent folders will be created.
	"""
	source = abspath(source)
	destination = abspath(destination)

	ensure_file_directory(destination)
	try:
		shutil.copy(source, destination)
	except shutil.SameFileError:
		pass

def move_tree(source: str, destination: str) -> None:
	"""
	Moves source file or directory to destination,
	which will be removed if already exists.
	"""
	ensure_file_directory(source)
	remove_tree(destination)
	shutil.move(source, destination)

def copy_directory(source: str, destination: str, clear_destination: bool = False, replacement: bool = True, ignore_list: Optional[Iterable[str]] = None, relative_path: Optional[str] = None) -> None:
	# XXX: Replace with shutil.copytree?
	if destination.startswith(source):
		raise ValueError(f"Destination {destination!r} cannot be copied into {source!r}!")

	if clear_destination:
		remove_tree(destination)
	ensure_directory(destination)

	for filename in os.listdir(source):
		relative_file = join(relative_path, filename) if relative_path else filename
		input = join(source, filename)
		if ignore_list:
			if filename in ignore_list or relative_file in ignore_list:
				continue
			if input in ignore_list:
				continue

		output = join(destination, filename)
		if isfile(input) and exists(output) and not replacement:
			continue

		if isdir(input):
			if isfile(output):
				os.remove(output)
			copy_directory(input, output, clear_destination, replacement, ignore_list, relative_file)
		elif not isdir(output):
			try: shutil.copy2(input, output)
			except shutil.SameFileError: pass

def merge_directory(source: str, destination: str, accept_squash: bool = True, ignore_list: Optional[Iterable[str]] = None, only_parent_ignore: bool = False, accept_replace: bool = True) -> None:
	"""
	Moves source file or directory to destination, optionally squashing
	or removes already existing files with same type.
	"""
	if ((isdir(source) and isfile(destination)) or (isdir(destination) and isfile(source))) and accept_squash:
		move_to_backup(destination)
	if not isdir(source):
		if not exists(destination):
			ensure_file_directory(destination)
			shutil.move(source, destination)
		return
	for filename in os.listdir(source):
		if ignore_list and filename in ignore_list:
			continue
		above = join(source, filename)
		behind = join(destination, filename)
		if isfile(above) and (not exists(behind) or (isfile(behind) and accept_replace)):
			if exists(behind):
				os.remove(behind)
			ensure_file_directory(behind)
			shutil.move(above, behind)
		elif isdir(above) or accept_squash:
			merge_directory(above, behind, accept_squash, ignore_list if not only_parent_ignore else list(), only_parent_ignore, accept_replace)

def walk_all_files(directories: Union[Iterable[str], str], then: Callable[[str], Any], extensions: Iterable[str] = ()) -> None:
	"""
	Recursively walks over directory contents and filters outputs
	if any extension provided in collection.
	"""
	def walk_files(directory: str):
		if isdir(directory):
			for dirpath, dirnames, filenames in os.walk(directory):
				for filename in filenames:
					has_extensions = False
					for extension in extensions:
						has_extensions = True
						if len(filename) >= len(extension) and filename[-len(extension):] == extension:
							then(join(dirpath, filename))
							break
					if not has_extensions:
						then(join(dirpath, filename))

	if isinstance(directories, str):
		walk_files(directories)
	elif isinstance(directories, list) or isinstance(directories, set):
		for directory in directories:
			walk_files(directory)

def get_all_files(directories: Union[Iterable[str], str], extensions: Iterable[str] = ()) -> List[str]:
	"""
	Recursively walks over directories contents and filters results
	if any extension provided in collection.
	"""
	files = list()
	walk_all_files(directories, lambda filename: files.append(filename), extensions)
	return files

def iterate_subdirectories(path: str, max_depth: int = 5, /, skip_yielding: bool = False):
	"""
	Recursively walks over directories until maximum depth exceed.
	"""
	if not isdir(path):
		return
	if not skip_yielding:
		yield path.rstrip("/\\")
	if max_depth <= 0 and max_depth != -1:
		return
	with os.scandir(path) as it:
		for entry in it:
			if entry.is_dir():
				yield entry.path
	with os.scandir(path) as it:
		for entry in it:
			if entry.is_dir():
				yield from iterate_subdirectories(entry.path, max_depth - 1 if max_depth > 0 else -1, skip_yielding=True)

def is_empty_directory(directory: str, non_existing_as_empty: bool = True) -> bool:
	if not isdir(directory):
		return non_existing_as_empty
	with os.scandir(directory) as entries:
		for _ in entries:
			return False
	return True

@overload
def ensure_not_whitespace(what: Optional[str], fallback: None = None) -> Optional[str]: ...
@overload
def ensure_not_whitespace(what: Optional[str], fallback: str) -> str: ...

def ensure_not_whitespace(what: Optional[str], fallback: Optional[str] = None) -> Optional[str]:
	"""
	Ensures that passed stroke is not none and contains any
	non-whitespace characters or returns fallback instead.
	"""
	return fallback if not what or len(what) == 0 or what.isspace() else what

def name_to_identifier(name: str, delimiter: str = "") -> str:
	"""
	Parses written name to identifier with alphabetic and decimal symbols.
	"""
	previous_char_lower = False
	previous_chars_upper = 0
	identifier = ""
	for char in name:
		if char.isalpha() or char.isdecimal() or char == ".":
			if (char.isupper() and previous_char_lower) or (char.islower() and previous_chars_upper > 1):
				identifier += delimiter
			identifier += char.lower()
		elif len(delimiter) > 0 and not identifier.endswith(delimiter):
			identifier += delimiter
		previous_char_lower = char.islower()
		previous_chars_upper = previous_chars_upper + 1 if char.isupper() else 0
	return identifier

def get_project_folder_by_name(directory: str, name: str) -> Optional[str]:
	"""
	Returns project name, which contains only alphabetic and decimal symbols.
	Fails if shrinked source is empty, e.g. name is whitespace or symbols.
	"""
	folder = name_to_identifier(name, "-").strip("-")
	return get_next_filename(directory, folder, "-") if len(folder) > 0 else None

def get_next_filename(directory: str, name: str, delimiter: str = "", extension: str = "", start_index: int = 1) -> str:
	"""
	Recursive checking existing pathes, counting additional postfix, which
	is separated from provided name with delimiter.
	"""
	buffer_name = name + extension
	buffer_index = start_index
	while exists(join(directory, buffer_name)):
		buffer_name = f"{name}{delimiter}{buffer_index}{extension}"
		buffer_index += 1
	return buffer_name

def shortcodes(source: str) -> str:
	"""
	- {datestamp} -> 20231201
	- {timestamp} -> 1745
	"""
	if not isinstance(source, str):
		return source
	from datetime import datetime
	date = datetime.now()
	source = source.replace("{datestamp}", date.strftime("%Y%m%d"))
	source = source.replace("{timestamp}", date.strftime("%H%M"))
	return source

def relativize_or_hash_path(target_path: str, relative_path: str) -> str:
	relative_path = relpath(target_path, relative_path)
	if not relative_path.startswith(".."):
		return relative_path
	from .output_directory import unique_folder_name
	return unique_folder_name(target_path)

def request_tool(name: str) -> Optional[str]:
	from .context import GLOBALS
	relative_path = GLOBALS.TOOLCHAIN_CONFIG.get_value(f"tools.{name}")
	if ensure_not_whitespace(relative_path):
		tool_path = GLOBALS.TOOLCHAIN_CONFIG.get_path(relative_path)
		if not exists(tool_path):
			from .output_directory import get_config_directory
			tool_path = join(get_config_directory(), relative_path)
		if exists(tool_path):
			return tool_path
	relative_path = shutil.which(name)
	if not relative_path or not ensure_not_whitespace(relative_path):
		return None
	return abspath(relative_path)

def request_executable_version(executable: Union[str, List[str]]) -> float:
	pattern_version = re.compile(r"\d+\.\d+")
	if isinstance(executable, str):
		executable = [executable]
	result = subprocess.run(executable + [
		"--version"
	], text=True, capture_output=True)
	output = result.stdout + result.stderr
	if result.returncode == 0 and output:
		match = pattern_version.search(output)
		if match:
			return float(match.group())
	result = subprocess.run(executable + [
		"-version"
	], text=True, capture_output=True)
	output = result.stdout + result.stderr
	if result.returncode == 0 and output:
		match = pattern_version.search(output)
		if match:
			return float(match.group())
	return 0.0

def parse_properties_property(line: str) -> Tuple[str, str]:
	try:
		key, value = line.strip().split("=", 1)
		return key.rstrip(), value.lstrip()
	except ValueError as exc:
		return "", str(exc)

def read_properties_stream(io: TextIO, skip_duplicates: bool = False, error_handler: Optional[Callable[[str], None]] = None) -> Dict[str, str]:
	properties = dict()
	line = io.readline()
	while line:
		key, value = parse_properties_property(line)
		if len(key) != 0:
			if not skip_duplicates or key not in properties:
				properties[key] = value
		elif error_handler:
			error_handler(value)
		line = io.readline()
	return properties

def encode_int(x: int, base: int, alphabet: str = "0123456789abcdefghijklmnopqrstuvwxyz") -> str:
	if x < 0:
		x *= 1
	elif x == 0:
		return alphabet[0]
	letters = []
	while x:
		letters.append(alphabet[x % base])
		x //= base
	letters.reverse()
	return "".join(letters)

class AttributeZipFile(ZipFile):
	def _extract_member(self, member: Union[ZipInfo, str], targetpath: str, pwd: Optional[str]) -> str:
		if not isinstance(member, ZipInfo):
			member = self.getinfo(member)

		targetpath = super()._extract_member(member, targetpath, pwd) # type: ignore

		attr = member.external_attr >> 16
		if platform.system() == "Windows":
			attr |= 0o0000200 | 0o0000020 # issues/17
		if attr != 0:
			os.chmod(abspath(targetpath), attr)
		return targetpath

def get_os_bitness() -> int:
	if platform.system() == "Windows":
		if "PROCESSOR_ARCHITEW6432" in os.environ:
			return 64
		arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()
		if "64" in arch or "AMD64" in arch:
			return 64
		return 32
	bitness_str, _ = platform.architecture()
	if "64" in bitness_str:
		return 64
	machine = platform.machine().lower()
	if "64" in machine or "x86_64" in machine or "amd64" in machine or "arm64" in machine:
		return 64
	return 32
