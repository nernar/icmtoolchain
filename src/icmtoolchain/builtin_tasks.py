import subprocess
from typing import Optional

from .context import GLOBALS, PROPERTIES
from .errors import abort
from .language import PROJECT_TYPE_MODPACK
from .logger import attention, failure, frozen, success, warn
from .output_directory import get_temporary_directory
from .shell import confirm_prompt
from .task import (TASK_MODE_GLOBALLY, TASK_MODE_ONCE_EARLY,
                   TASK_MODE_ONCE_LATELY, task)
from .utils import DEVNULL

### JAVASCRIPT, TYPESCRIPT, JAVA, C++

@task(
	"buildScripts",
	locks=["script", "cleanup", "push"],
	description="Recompiles scripts using file concatenation, TypeScript Compiler or Babel."
)
def task_build_scripts() -> int:
	if not GLOBALS.MAKE_CONFIG.supports_scripts:
		return 0
	from .script_build import build_all_scripts
	return build_all_scripts()

@task(
	"updateIncludes",
	description="Overrides the contents of 'tsconfig.json' based on script files."
)
def task_update_includes() -> int:
	if not GLOBALS.MAKE_CONFIG.supports_scripts:
		return 0
	from .script_build import collect_script_repository_and_coerce
	collect_script_repository_and_coerce()
	return 0

@task(
	"compileJava",
	locks=["java", "cleanup", "push"],
	description="Compiles java folders using Gradle, Javac or ECJ."
)
def task_compile_java(tool: Optional[str] = None) -> int:
	if not GLOBALS.MAKE_CONFIG.supports_java:
		return 0
	from .java_build import compile_java
	if tool != "gradle" and GLOBALS.MAKE_CONFIG.get_value("java.configurable", False):
		if tool:
			attention(f"Project uses configurable Gradle, different tooling ({tool}) cannot be applied.")
		tool = "gradle"
	if not tool:
		tool = GLOBALS.MAKE_CONFIG.get_value("java.compiler", "javac")
	return compile_java(tool)

@task(
	"compileNative",
	locks=["native", "cleanup", "push"],
	description="Compiles native folders using NDK and links objects."
)
def task_compile_native() -> int:
	if not GLOBALS.MAKE_CONFIG.supports_native:
		return 0
	abis = None
	if not PROPERTIES.get_value("release"):
		abi = GLOBALS.MAKE_CONFIG.get_value("native.debugAbi")
		if not abi:
			abi = GLOBALS.MAKE_CONFIG.get_value("debugAbi")
		if abi:
			attention("Property `debugAbi` has been deprecated in favor of configurations, determine your own ABIs via 'debug' rule.")
			abis = [abi]
	if not abis:
		abis = GLOBALS.MAKE_CONFIG.obtain_list("native.abis")
		if len(abis) == 0:
			abis = GLOBALS.MAKE_CONFIG.obtain_list("abis")
	if len(abis) == 0:
		abis = ["arm64-v8a", "armeabi-v7a"]
	from .native_build import compile_native, copy_shared_objects
	result = compile_native(abis)
	if result == 0 and GLOBALS.MAKE_CONFIG.supports_shared_objects:
		result = copy_shared_objects(abis)
	return result

### RESOURCES

@task(
	"buildResources",
	locks=["resource", "cleanup", "push"],
	description="Copies predefined resources consisting of textures, in-game packs, etc."
)
def task_resources() -> int:
	from .resources import (build_additional_resources, build_pack_graphics,
	                        build_resources)
	overall_result = 0
	if GLOBALS.MAKE_CONFIG.supports_resources:
		overall_result = build_resources()
	if overall_result == 0 and GLOBALS.MAKE_CONFIG.supports_pack_graphics:
		overall_result = build_pack_graphics()
	if overall_result == 0:
		overall_result = build_additional_resources()
	if overall_result == 0:
		GLOBALS.LINKED_RESOURCE_STORAGE.save_contents()
	return overall_result

### PACKAGING & CLEAN UP

@task(
	"clearOutput",
	locks=["assemble", "push", "native", "java", "resource", "script"],
	description="Optionally deletes the output folder; has no effect by default."
)
def task_clear_output(force: bool = False) -> int:
	from .package import pretty_cleanup_directory
	if PROPERTIES.get_value("release"):
		pretty_cleanup_directory(GLOBALS.MAKE_CONFIG.get_build_path())
	if force or GLOBALS.MAKE_CONFIG.get_value("development.clearOutput", False):
		GLOBALS.PROJECT_STRUCTURE.cleanup(clear_output=True)
	if force or PROPERTIES.get_value("release"):
		pretty_cleanup_directory(GLOBALS.PROJECT_STRUCTURE.directory)
	return 0

@task(
	"buildInfo",
	locks=["cleanup", "push"],
	description="Writes the description file 'mod.info' to output folder for display in mod browser."
)
def task_build_info() -> int:
	project_data = GLOBALS.MAKE_CONFIG.obtain_project_data()
	if project_data is None:
		attention(f"Project type could not be determined from config, considering it should be mod!")
		warn("Please add property `info` for mod, `modpack` for modpack or `manifest` for pack into your 'make.json'.")
		return 0
	return project_data.flush_to_output(GLOBALS.PROJECT_STRUCTURE.directory)

@task(
	"buildPackage",
	locks=["push", "assemble", "native", "java", "resource", "script"],
	description="Assembles project's output folder into an archive, specifically for publishing in a mod browser."
)
def task_build_package(all_sides: bool = False) -> int:
	from .resources import build_package
	if not all_sides:
		active_side = PROPERTIES.get_value("side")
		return build_package(side=active_side)

	results = []
	for side in (None, "client", "server"):
		if side:
			GLOBALS.MAKE_CONFIG.bisect_properties(side)
		else:
			GLOBALS.MAKE_CONFIG.remove_rules("side")
		results.append(build_package(side=side))

	GLOBALS.MAKE_CONFIG.remove_rules("side")
	return max(results)

### DEPLOY

@task(
	"pushEverything",
	locks=["push"],
	description="Sends assembled output folder to a connected device."
)
def task_push_everything() -> int:
	from .push import push_everything
	return push_everything()

@task(
	"launchApplication",
	description="Starts launcher with predefined autostart setting on a connected device using ADB.",
	mode=TASK_MODE_ONCE_LATELY
)
def task_monkey_launcher() -> int:
	from .adb import ensure_device_ready
	if not ensure_device_ready():
		return 1

	preferred_launcher = GLOBALS.PREFERRED_CONFIG.get_value("adb.launcherPackage")
	preferred_activity = GLOBALS.PREFERRED_CONFIG.get_value("adb.launcherActivity")
	from .adb import (LAUNCHER_PACKAGES, launch_package_via_am,
	                  launch_package_via_monkey)
	packages = (preferred_launcher, ) if preferred_launcher else LAUNCHER_PACKAGES

	subprocess.run(GLOBALS.ADB_COMMAND + [
		"shell", "input",
		"keyevent", "KEYCODE_WAKEUP"
	], stdout=DEVNULL, stderr=DEVNULL)

	for package in packages:
		activity = preferred_activity
		if not activity and "horizon" in package:
			activity = "com.zhekasmirnov.horizon.activity.main.StartupWrapperActivity"

		launched = False
		if activity:
			launched = launch_package_via_am(package, activity)
		if not launched:
			launched = launch_package_via_monkey(package)

		if launched:
			success(f"Successfully launched {package!r}!")
			return 0

	failure("Horizon is not installed, nothing to launch.")
	return 1

@task(
	"stopApplication",
	description="Terminates launcher process on a connected device using ADB.",
	mode=TASK_MODE_ONCE_EARLY
)
def task_stop_launcher() -> int:
	preferred_launcher = GLOBALS.PREFERRED_CONFIG.get_value("adb.launcherPackage")
	from .adb import LAUNCHER_PACKAGES
	packages = (preferred_launcher, ) if preferred_launcher else LAUNCHER_PACKAGES

	try:
		for package in packages:
			subprocess.run(GLOBALS.ADB_COMMAND + [
				"shell", "am", "force-stop", package
			], check=True, stdout=DEVNULL, stderr=DEVNULL)
	except subprocess.CalledProcessError as err:
		return err.returncode
	return 0

@task(
	"configureADB",
	description="Adds a new connection to a mobile device/emulator via cable or network.",
	mode=TASK_MODE_GLOBALLY
)
def task_configure_adb() -> int:
	from .device_setup import setup_device_connection
	setup_device_connection()
	return 0

### MISCELLANEOUS

@task(
	"ensureProjectExists",
	description="Ensures that selected project is opened and exists."
)
def task_ensure_project_exists() -> int:
	if GLOBALS.is_project_available():
		return 0
	abort("Not found any project in your directory. Try passing `--project <path>` into this command.")

### TOOLCHAIN

@task(
	"newProject",
	description="Creates a project, prompting interactive input for name, template, and other properties.",
	mode=TASK_MODE_GLOBALLY
)
def task_new_project() -> int:
	from .package import request_create_project
	index = request_create_project(GLOBALS.PREFERRED_CONFIG.get_value("defaultTemplate", "../toolchain-mod"))
	if index is None:
		return 1
	success("Successfully completed!")
	return 0

@task(
	"configureIde",
	description="Configures tasks in most useful IDEs for mods to use from the interface.",
	mode=TASK_MODE_GLOBALLY
)
def task_configure_ide(exclude_toolchain: bool = False) -> int:
	from .workspace import flush_compound_tasks, flush_toolchain_tasks

	is_workspace_or_modpack = GLOBALS.MAKE_CONFIG.project_type == PROJECT_TYPE_MODPACK
	assemble_task_name = "Assemble for Release" if not is_workspace_or_modpack else "Pack for Release"

	flush_toolchain_tasks(assemble_task_name, "package", "--release ensureProjectExists clearOutput --force buildScripts compileNative compileJava buildResources buildInfo buildPackage")

	if GLOBALS.MAKE_CONFIG.project_type == PROJECT_TYPE_MODPACK:
		flush_toolchain_tasks(f"{assemble_task_name} (Client)", "package", "--release --side client ensureProjectExists clearOutput --force buildScripts compileNative compileJava buildResources buildInfo buildPackage", hidden=True)
		flush_toolchain_tasks(f"{assemble_task_name} (Server)", "package", "--release --side server ensureProjectExists clearOutput --force buildScripts compileNative compileJava buildResources buildInfo buildPackage", hidden=True)
		flush_toolchain_tasks(f"{assemble_task_name} (Both)", "package", "--release ensureProjectExists clearOutput --force buildScripts compileNative compileJava buildResources buildInfo buildPackage --all-sides", hidden=True)

	flush_toolchain_tasks("Build (No push)", "run", "ensureProjectExists clearOutput buildScripts compileNative compileJava buildResources buildInfo", hidden=True)
	flush_compound_tasks("Build", "run", ("Build (No push)", "Push"))

	if GLOBALS.MAKE_CONFIG.supports_scripts:
		flush_toolchain_tasks("Build Scripts (No push)", "code", "ensureProjectExists buildScripts buildInfo", hidden=True)
		flush_compound_tasks("Build Scripts", "code", ("Build Scripts (No push)", "Push"))
		if GLOBALS.MAKE_CONFIG.supports_resources or GLOBALS.MAKE_CONFIG.supports_pack_graphics:
			flush_toolchain_tasks("Build Scripts and Resources (No push)", "debug-alt", "ensureProjectExists clearOutput buildScripts buildResources buildInfo", hidden=True)
			flush_compound_tasks("Build Scripts and Resources", "debug-alt", ("Build Scripts and Resources (No push)", "Push"))
		flush_toolchain_tasks("Rebuild Declarations", "type-hierarchy-sub", "ensureProjectExists updateIncludes", hidden=True)

	if GLOBALS.MAKE_CONFIG.supports_java:
		flush_toolchain_tasks("Build Java (No push)", "circuit-board", "ensureProjectExists compileJava buildInfo", hidden=True)
		flush_compound_tasks("Build Java", "circuit-board", ("Build Java (No push)", "Push"))

	if GLOBALS.MAKE_CONFIG.supports_native or GLOBALS.MAKE_CONFIG.supports_shared_objects:
		flush_toolchain_tasks("Build Native (No push)", "chip", "ensureProjectExists compileNative buildInfo", hidden=True)
		flush_compound_tasks("Build Native", "chip", ("Build Native (No push)", "Push"))

	if GLOBALS.MAKE_CONFIG.supports_resources or GLOBALS.MAKE_CONFIG.supports_pack_graphics:
		flush_toolchain_tasks("Build Resources (No push)", "paintcan", "ensureProjectExists clearOutput buildResources buildInfo", hidden=True)
		flush_compound_tasks("Build Resources", "paintcan", ("Build Resources (No push)", "Push"))

	flush_toolchain_tasks("Push", "rocket", "ensureProjectExists stopApplication pushEverything launchApplication")
	flush_toolchain_tasks("Invalidate Caches", "flame", "cleanup", focus=True)

	if not exclude_toolchain:
		flush_toolchain_tasks("New Project", "new-folder", "newProject", focus=True)
		flush_toolchain_tasks("Configure ADB", "device-mobile", "configureADB", focus=True)
		flush_toolchain_tasks("Configure Components", "tools", "componentIntegrity", focus=True)

	return 0

@task(
	"componentIntegrity",
	description="Installs additional components required for compilation or performs a initial setup.",
	mode=TASK_MODE_GLOBALLY
)
def task_component_integrity(startup: bool = False) -> int:
	if startup:
		from .component import startup as component_task
	else:
		from .component import upgrade as component_task
	component_task()
	return 0

@task(
	"cleanup",
	locks=["assemble", "push", "native", "java", "resource", "script"],
	description="Clears cache of a selected project or all output files from previous builds, forgetting modified files.",
	mode=TASK_MODE_GLOBALLY
)
def task_cleanup() -> int:
	from .package import pretty_cleanup_directory
	if GLOBALS.is_project_available():
		pretty_cleanup_directory(GLOBALS.MAKE_CONFIG.get_build_path())
		GLOBALS.PROJECT_STRUCTURE.cleanup(clear_output=True)
		pretty_cleanup_directory(GLOBALS.PROJECT_STRUCTURE.directory)
		return 0
	if not confirm_prompt("Do you want to clear all projects cache?", True):
		frozen("Abort.")
		return 0
	pretty_cleanup_directory(get_temporary_directory())
	return 0
