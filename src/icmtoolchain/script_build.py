from os.path import basename, exists, isdir, isfile, join, splitext
from time import time
from typing import TYPE_CHECKING, Any, MutableMapping, Optional

from .context import GLOBALS, PROPERTIES
from .language import MakeScriptData
from .logger import attention, debug, failure, frozen, success
from .output_directory import expand_paths
from .utils import RuntimeCodeError, ensure_not_whitespace, walk_all_files

if TYPE_CHECKING:
	from .script_transpiler import ScriptRepository


def build_all_scripts() -> int:
	GLOBALS.PROJECT_STRUCTURE.cleanup_target("scripts")
	GLOBALS.PROJECT_STRUCTURE.cleanup_target("libraries")

	from .script_setup import request_typescript
	tsc_available = request_typescript(only_check=True) is not None
	if tsc_available and not isdir(GLOBALS.TOOLCHAIN_CONFIG.get_relative_path("declarations")):
		from .output_directory import get_config_directory
		if not isdir(join(get_config_directory(), "declarations")):
			attention("Not found 'declarations', in most cases build will be failed, please install it via tasks.")

	return transpile_project(tsc_available=tsc_available)

def declare_build_target(source: MakeScriptData, target_path: str) -> str:
	declare: MutableMapping[str, Any] = {
		# make.json source type -> build.config source type
		"sourceType": "mod" if source.type == "main" else "custom" if source.type == "instant" else source.type
	}

	if ensure_not_whitespace(source.api) and source.type != "preloader":
		declare["api"] = source.api
	if source.optimization_level != -1:
		declare["optimizationLevel"] = min(max(int(source.optimization_level), -1), 9)
	if ensure_not_whitespace(source.source_name):
		declare["sourceName"] = source.source_name

	target_type = "libraries" if source.type == "library" else "scripts"
	return GLOBALS.PROJECT_STRUCTURE.declare_target(
		keyword=target_type,
		relative_path=target_path,
		declare=declare
	).absolute_path

def detect_source_language(source_path: str) -> Optional[str]:
	detected_javascript = False
	detected_typescript = False

	if not isdir(source_path):
		detected_javascript = source_path.endswith(".js")
		detected_typescript = source_path.endswith(".ts") and not source_path.endswith(".d.ts")

	else:
		def walk(file: str) -> None:
			nonlocal detected_javascript
			if file.endswith(".ts") and not file.endswith(".d.ts"):
				raise ValueError()
			if not detected_javascript and file.endswith(".js"):
				detected_javascript = True

		try:
			walk_all_files(source_path, walk)
		except ValueError:
			detected_typescript = True

	if not (detected_javascript or detected_typescript):
		return None
	return "typescript" if detected_typescript else "javascript"

def request_type_check() -> int:
	type_check_mode = GLOBALS.PREFERRED_CONFIG.get_value("typeChecking", "always")
	should_type_check = (
		type_check_mode == "always"
		or (type_check_mode == "on-release" and PROPERTIES.get_value("release"))
	)

	if should_type_check:
		debug("Running type-check via tsc --noEmit")
		startup_millis = time()
		overall_result = GLOBALS.TSC_COMPOSITE.type_check(*(
			["--force"] if PROPERTIES.get_value("release") else []
		))
		startup_millis = time() - startup_millis

		if overall_result != 0:
			failure(f"Type-check failed in {startup_millis:.2f}s with result {overall_result}.")
			return overall_result
		success(f"Type-check completed in {startup_millis:.2f}s.")
	return 0

def collect_script_repository() -> 'ScriptRepository':
	from .script_transpiler import ScriptRepository
	repository = ScriptRepository()
	from .script_setup import request_typescript, should_use_babel
	use_babel = should_use_babel()
	from .includes import Includes

	for source in GLOBALS.MAKE_CONFIG.iterate_scripts():
		includes_path = ensure_not_whitespace(source.includes_path, ".includes")
		is_pattern_path = source.relative_path.endswith("*")

		for source_path in expand_paths(GLOBALS.MAKE_CONFIG.get_relative_path(source.relative_path)):
			if not exists(source_path):
				attention(f"Skipped non-existing source {GLOBALS.MAKE_CONFIG.get_path_to_config(source_path)!r}!")
				continue

			# Supports assembling directories, JavaScript and TypeScript
			detected_language = detect_source_language(source_path)
			if not detected_language:
				attention(f"Unsupported script {GLOBALS.MAKE_CONFIG.get_path_to_config(source_path)!r}, it should be directory with includes or Java/TypeScript file!")
				continue

			# Developer can deny to install TypeScript/Node.js or forcefully deny it
			language = ensure_not_whitespace(source.language, detected_language)
			if language == "typescript" and not request_typescript():
				if detected_language == "typescript" and GLOBALS.TOOLCHAIN_CONFIG.get_value("denyTypeScript"):
					raise RuntimeCodeError(255, f"We cannot compile source {GLOBALS.MAKE_CONFIG.get_path_to_config(source_path)!r} without you having Node.js, despite `denyTypeScript` property of your 'toolchain.json' is being active. Please disable it and install Node.js to compile TypeScript sources.")
				attention(f"Source {GLOBALS.MAKE_CONFIG.get_path_to_config(source_path)!r} specifies target language as TypeScript, so this script probably uses ESNext capabilities. Building as normal JavaScript files, since `denyTypeScript` property of your 'toolchain.json' is active.")
				language = "javascript"

			if not is_pattern_path:
				prefixed_path = source.output_path
			else:
				script_name = basename(source_path)
				if isfile(source_path):
					script_name = splitext(script_name)[0]
				prefixed_path = join(source.output_path, script_name + ".js")

			# Preserve output target duplication
			try:
				dot_index = prefixed_path.rindex(".")
				prefixed_path = prefixed_path[:dot_index] + "{}" + prefixed_path[dot_index:]
			except ValueError:
				prefixed_path += "{}"

			destination_path = declare_build_target(source, prefixed_path)
			transpiler = "concat" if language == "javascript" else "babel" if use_babel else "tsc"

			if isdir(source_path):
				includes = Includes.invalidate(source_path, includes_path)
				repository.include_directory(includes, destination_path, transpiler)
			elif isfile(source_path):
				repository.include_file(source_path, destination_path, transpiler)

	if PROPERTIES.get_value("debug"):
		debug(f"Files ({len(repository.files)}): {', '.join([str(file) for file in repository.files])}")
		debug(f"Directories ({len(repository.directories)}): {', '.join([str(directory) for directory in repository.directories])}")

	return repository

def transpile_project(tsc_available: bool = False) -> int:
	from .script_transpiler import iterate_transpilers
	repository = collect_script_repository()

	overall_result = 0
	if tsc_available:
		for source in repository.files:
			GLOBALS.TSC_COMPOSITE.coerce(source.source_path)
		for source in repository.directories:
			GLOBALS.TSC_COMPOSITE.reference(source.includes.directory)
		GLOBALS.TSC_COMPOSITE.flush()

		from .script_setup import should_use_babel
		use_babel = should_use_babel()
		if use_babel:
			overall_result += request_type_check()
			if overall_result != 0:
				return overall_result

	for transpiler in iterate_transpilers():
		overall_result += transpiler.transpile(repository)
	if overall_result != 0:
		return overall_result

	for source in repository.iterate():
		if not isfile(source.destination_path):
			failure(f"No suitable transpiler {source.transpiler!r} could be found for {GLOBALS.MAKE_CONFIG.get_path_to_config(source.source_path)!r}!")
			overall_result += 1
		elif not GLOBALS.BUILD_STORAGE.is_path_changed(source.destination_path):
			frozen(f"Build target {GLOBALS.MAKE_CONFIG.get_path_to_config(source.source_path)!r} is not changed.")
	if overall_result != 0:
		return overall_result

	GLOBALS.PROJECT_STRUCTURE.generate_config()
	return overall_result
