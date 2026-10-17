from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from os.path import abspath, basename, dirname, isdir, isfile, join
from typing import (TYPE_CHECKING, Any, Callable, Dict, Final, Iterable,
                    MutableMapping, Optional, Type, Union)

from .config import Config, FileConfig
from .context import GLOBALS
from .errors import abort
from .logger import attention
from .output_directory import expand_paths
from .rule_set import RuleSet, RuleSetConfig, RuleSetHolder
from .utils import RuntimeCodeError, copy_file, ensure_not_whitespace

if TYPE_CHECKING:
	from .project_graph import Artifact

def get_language_directories(compile_type: str, language_config: Config, properties_merger: Optional[Callable] = None, make_config: Optional['MakeDataConfig'] = None) -> Dict[str, Config]:
	if not make_config:
		make_config = GLOBALS.MAKE_CONFIG
	assert make_config is not None

	directories = language_config.obtain_list("directories")
	if not any(directories):
		# Obtain directories from deprecated `compile` property.
		directories = list(filter(
			lambda source: isinstance(source, Config) and compile_type == source.get_value("type", lambda: source.get_value("sourceType")),
			make_config.obtain_list("compile")
		))
	configurables = dict()
	if not any(directories):
		return configurables
	language_config.delete_value("directories")

	for directory in directories:
		config = None

		if isinstance(directory, Config):
			directory.defaults = config
			config = directory
			if "path" in directory:
				directory = directory.get_value("path")
			elif "source" in directory:
				directory = directory.get_value("source")
		if not isinstance(directory, str):
			raise RuntimeCodeError(1, f"Wrong declared {compile_type} directory {directory!r}, it should be path string or object with `path` property!")

		for flattened_directory in expand_paths(make_config.get_relative_path(directory)):
			absolute_directory = make_config.get_path(flattened_directory)
			if not isdir(absolute_directory):
				attention(f"Skipped non-existing {compile_type} directory {directory!r}!")
				continue
			if absolute_directory in configurables:
				attention(f"Duplicate {compile_type} directory {directory!r}, overriding existing properties...")

			if properties_merger:
				config = properties_merger(config, language_config)
			else:
				temporary_config = Config(deepcopy(language_config.as_json()))
				if config:
					temporary_config.merge_config(config, exclusive_lists=True)
				config = temporary_config
			config.set_value("directory", make_config.get_path_to_config(flattened_directory))
			configurables[absolute_directory] = config

	return configurables

@dataclass
class FlushableMakeProjectData(ABC):
	@abstractmethod
	def flush_to_output(self, directory: str) -> int:
		...

@dataclass
class MakeModData(FlushableMakeProjectData):
	name: str
	author: str
	version: str = "1.0"
	description: str = ""
	icon: Optional[str] = "mod_icon.png"
	client_only: bool = False
	server_only: bool = False

	def flush_to_output(self, directory: str) -> int:
		info_file = join(directory, "mod.info")
		info = FileConfig(info_file, do_not_read=True)
		if ensure_not_whitespace(self.name):
			info.set_value("name", self.name)
		if ensure_not_whitespace(self.author):
			info.set_value("author", self.author)
		if ensure_not_whitespace(self.version):
			info.set_value("version", self.version)
		if ensure_not_whitespace(self.description):
			info.set_value("description", self.description)
		if self.client_only:
			info.set_value("clientOnly", True)
		elif self.server_only:
			info.set_value("serverOnly", True)
		info.save_as_file(indent="\t")

		icon_path = GLOBALS.MAKE_CONFIG.get_path(self.icon or "mod_icon.png")
		output_info_path = join(directory, "mod_icon.png")
		if isfile(icon_path) and icon_path != output_info_path:
			copy_file(icon_path, output_info_path)
		elif ensure_not_whitespace(self.icon):
			attention(f"Mod icon {icon_path!r} described in {self.name!r} is not found!")
		return 0

@dataclass
class MakeModpackData(FlushableMakeProjectData):
	name: str
	displayed_name: Union[MutableMapping[str, str], str]
	author: Union[MutableMapping[str, str], str]
	version_name: Union[MutableMapping[str, str], str] = "1.0"
	version_code: int = 1
	description: Union[MutableMapping[str, str], str] = ""
	icon: Optional[str] = None

	def flush_to_output(self, directory: str) -> int:
		manifest_file = join(directory, "modpack.json")
		manifest = FileConfig(manifest_file, do_not_read=False)
		if ensure_not_whitespace(self.name):
			manifest.set_value("packName", self.name)
		if isinstance(self.displayed_name, MutableMapping) or ensure_not_whitespace(self.displayed_name):
			manifest.set_value("displayedName", self.displayed_name)
		if isinstance(self.version_name, MutableMapping) or ensure_not_whitespace(self.version_name):
			manifest.set_value("versionName", self.version_name)
		if self.version_code >= 0:
			manifest.set_value("versionCode", self.version_code)
		if isinstance(self.author, MutableMapping) or ensure_not_whitespace(self.author):
			manifest.set_value("author", self.author)
		if isinstance(self.description, MutableMapping) or ensure_not_whitespace(self.description):
			manifest.set_value("description", self.description)
		manifest.save_as_file()

		icon_path = GLOBALS.MAKE_CONFIG.get_path(self.icon or "pack_icon.png")
		output_info_path = join(directory, "pack_icon.png")
		if isfile(icon_path) and icon_path != output_info_path:
			copy_file(icon_path, output_info_path)
		elif ensure_not_whitespace(self.icon):
			attention(f"Modpack icon {icon_path!r} described in {self.name!r} is not found!")
		return 0

@dataclass
class MakePackData(FlushableMakeProjectData):
	name: str
	version: str
	description: Union[MutableMapping[str, str], str] = ""
	manifest: MutableMapping[str, Any] = field(default_factory=Config)

	def flush_to_output(self, directory: str) -> int:
		output_manifest_path = join(directory, "manifest.json")
		manifest = FileConfig(output_manifest_path, map=self.manifest, do_not_read=True)
		if ensure_not_whitespace(self.name):
			manifest.set_value("pack", self.name)
		if ensure_not_whitespace(self.version):
			manifest.set_value("packVersion", self.version)
		if isinstance(self.description, MutableMapping) or ensure_not_whitespace(self.description):
			manifest.set_value("description", self.description)
		manifest.save_as_file(output_manifest_path)
		return 0

@dataclass
class MakeScriptData:
	relative_path: str
	output_path: str
	type: str = "main"
	language: Optional[str] = None
	source_name: Optional[str] = None
	api: str = "CoreEngine"
	includes_path: str = ".includes"
	optimization_level: int = -1

@dataclass
class MakeJavaData:
	relative_path: str
	output_path: str
	sources: Iterable[str]
	libraries: Iterable[str] = field(default_factory=list)
	classpath: Iterable[str] = field(default_factory=list)
	verbose: bool = False
	keep_libraries: bool = False
	keep_sources: bool = False
	options: Iterable[str] = field(default_factory=list)

@dataclass
class MakeNativeData:
	relative_path: str
	output_path: str
	shared_name: str
	depends: Iterable[str]
	link: Iterable[str] = field(default_factory=list)
	link_static: Iterable[str] = field(default_factory=list)
	include: Iterable[str] = field(default_factory=list)
	stdincludes: Iterable[str] = field(default_factory=list)
	keep_includes: bool = False
	keep_sources: bool = False
	options: Iterable[str] = field(default_factory=list)

@dataclass
class MakeSharedObjectData:
	relative_path: str

@dataclass
class MakeResourceData:
	relative_path: str
	output_path: str # XXX: Unused, at least for now.
	type: str = "resource_directory"
	push_unchanged_files: bool = True
	cleanup_remote: bool = True

@dataclass
class MakePackGraphicsData:
	group_name: str
	images: Iterable[str]

@dataclass
class MakeAssetData:
	relative_path: str
	output_path: str
	output_filename: Optional[str] = None
	push_unchanged_files: bool = True
	cleanup_remote: bool = True

AVAILABLE_DATA_CONFIGS: Dict[Union[str, Callable[[str], str]], Type['MakeDataConfig']] = {}

PROJECT_TYPE_UNKNOWN = 0
PROJECT_TYPE_MOD = 1
PROJECT_TYPE_MODPACK = 2
PROJECT_TYPE_PACK = 3

class ModpackDataMixin:
	def obtain_mod_data(self, mod_info: Config) -> MakeModData:
		name = mod_info.get_value("name") or ""
		author = mod_info.get_value("author") or ""
		version = mod_info.get_value("version") or ""
		description = mod_info.get_value("description") or ""
		icon = mod_info.get_value("icon")
		client_only = bool(mod_info.get_value("clientOnly", False))
		server_only = bool(mod_info.get_value("serverOnly", False))

		from .utils import shortcodes
		return MakeModData(
			name=shortcodes(name),
			author=author,
			version=shortcodes(version),
			description=shortcodes(description),
			icon=icon,
			client_only=client_only,
			server_only=server_only
		)

	def shortcodes_mapping(self, mapping: Union[MutableMapping[str, str], str]) -> Union[MutableMapping[str, str], str]:
		from .utils import shortcodes
		if isinstance(mapping, MutableMapping):
			for key, value in mapping.items():
				mapping[key] = shortcodes(value)
		elif isinstance(mapping, str):
			return shortcodes(mapping)
		return mapping

	def obtain_pack_data(self, manifest: Config) -> MakePackData:
		name = manifest.get_value("pack") or ""
		version = manifest.get_value("packVersion") or ""
		description = manifest.get_value("description") or ""

		from .utils import shortcodes
		return MakePackData(
			name=shortcodes(name),
			version=shortcodes(version),
			description=self.shortcodes_mapping(description),
			manifest=manifest
		)

	def obtain_modpack_data(self, manifest: Config) -> MakeModpackData:
		name = manifest.get_value("packName", lambda: manifest.get_value("name")) or ""
		displayed_name = manifest.get_value("displayedName") or ""
		version_name = manifest.get_value("versionName") or ""
		version_code = manifest.get_value("versionCode", 1)
		author = manifest.get_value("author") or ""
		description = manifest.get_value("description") or ""

		from .utils import shortcodes
		return MakeModpackData(
			name=shortcodes(name),
			displayed_name=self.shortcodes_mapping(displayed_name),
			author=self.shortcodes_mapping(author),
			version_name=self.shortcodes_mapping(version_name),
			version_code=version_code,
			description=self.shortcodes_mapping(description)
		)

class JavaConfigMixin:
	@property
	def supports_java(self) -> bool:
		return False

	def iterate_java(self, defaults: Optional[Config] = None) -> Iterable[MakeJavaData]:
		"""Returns iterable java data that is used in appropriate compilers and handlers.
		Java represents folders in their respective language, compiled using Javac.
		You are responsible for producing this data, using this config and manifests within a project.

		Returns:
			Iterable[MakeJavaData]: iterable which can be used in compilers
		"""
		return []

	def obtain_java_manifest_data(self, relative_path: str, output_path: str, config: Config) -> MakeJavaData:
		return MakeJavaData(
			relative_path=relative_path,
			output_path=output_path,
			sources=config.obtain_list("source-dirs"),
			libraries=config.obtain_list("library-dirs"),
			classpath=config.obtain_list("classpath"),
			verbose=config.get_value("verbose", False),
			keep_libraries=config.get_value("keepLibraries", False),
			keep_sources=config.get_value("keepSources", False),
			options=config.obtain_list("options")
		)

class NativeConfigMixin:
	@property
	def supports_native(self) -> bool:
		return False

	def iterate_native(self, defaults: Optional[Config] = None) -> Iterable[MakeNativeData]:
		"""Returns iterable native data that is used in appropriate compilers and handlers.
		Native represents folders that uses C/C++ languages, compiled using GNU GCC.
		You are responsible for producing this data, using this config and manifests within a project.

		Returns:
			Iterable[MakeNativeData]: iterable which can be used in compilers
		"""
		return []

	def obtain_native_manifest_data(self, relative_path: str, output_path: str, config: Config) -> MakeNativeData:
		# Obtain deprecated `rules` property to being merged.
		if "rules" in config:
			rules_config = config.get_value("rules")
			if isinstance(rules_config, Config):
				config.merge_config(rules_config)

		shared_name = config.get_value("shared.name", basename(relative_path))
		if not ensure_not_whitespace(shared_name) or ("shared" in config and shared_name == "unnamed"):
			raise ValueError(f"Library directory {relative_path!r} uses illegal name {shared_name!r}!")
		if config.get_value("library.version", -1) < 0 and "library" in config:
			raise ValueError(f"Library directory {relative_path!r} shared a library with illegal version!")

		return MakeNativeData(
			relative_path=relative_path,
			output_path=output_path,
			shared_name=shared_name,
			depends=config.obtain_list("depends"),
			link=config.obtain_list("link"),
			link_static=config.obtain_list("linkStatic"),
			include=config.obtain_list("include"),
			stdincludes=config.obtain_list("stdincludes"),
			keep_includes=config.get_value("keepIncludes", False),
			keep_sources=config.get_value("keepSources", False),
			options=config.obtain_list("options")
		)

class ScriptConfigMixin:
	@property
	def supports_scripts(self) -> bool:
		return False

	def iterate_scripts(self) -> Iterable[MakeScriptData]:
		"""Returns iterable script data that is used in appropriate compilers and handlers.
		Scripts are individual files or folders written using Java/TypeScript language.
		You are responsible for producing this data, using this config and manifests within a project.

		Returns:
			Iterable[MakeScriptData]: iterable which can be used in compilers
		"""
		return []

class SharedObjectsConfigMixin:
	@property
	def supports_shared_objects(self) -> bool:
		return False

	def iterate_shared_objects(self) -> Iterable[MakeSharedObjectData]:
		"""Returns iterable shared object data that is used in appropriate compilers and handlers.
		Shared objects are platform-specific native libraries. They are precompiled.
		You are responsible for producing this data, using this config and manifests within a project.

		Returns:
			Iterable[MakeSharedObjectData]: iterable which can be used in compilers
		"""
		return []

class ResourceConfigMixin:
	@property
	def supports_resources(self) -> bool:
		return False

	def iterate_resources(self) -> Iterable[MakeResourceData]:
		"""Returns iterable resource data that is used in appropriate compilers and handlers.
		Resources are textures, shaders, and other materials that extend a game. There are different types of resources.
		You are responsible for producing this data, using this config and manifests within a project.

		Returns:
			Iterable[MakeResourceData]: iterable which can be used in compilers
		"""
		return []

class PackGraphicsConfigMixin:
	@property
	def supports_pack_graphics(self) -> bool:
		return False

	def iterate_pack_graphics(self) -> Iterable[MakePackGraphicsData]:
		"""Returns iterable pack graphics data that is used in appropriate compilers and handlers.
		Pack graphics are usually images for interactive window interfaces and backgrounds in pack menus.
		You are responsible for producing this data, using this config and manifests within a project.

		Returns:
			Iterable[MakePackGraphicsData]: iterable which can be used in compilers
		"""
		return []

class SideAwareMixin:
	def get_project_side(self) -> Optional[str]:
		"""Returns 'client', 'server', or None (both sides).
		Priority: clientOnly > serverOnly > None.
		"""
		return None

	def is_side_compatible(self, active_side: Optional[str]) -> bool:
		"""Returns True if this project should be included for the given active side.

		Args:
			active_side: 'client', 'server', or None / 'both' (no filter).
		"""
		if not active_side or active_side == "both":
			return True
		project_side = self.get_project_side()
		if project_side is None:
			return True
		return project_side == active_side

class MakeDataConfig(FileConfig, RuleSetConfig, RuleSetHolder, ModpackDataMixin, SideAwareMixin, JavaConfigMixin, NativeConfigMixin, ScriptConfigMixin, SharedObjectsConfigMixin, ResourceConfigMixin, PackGraphicsConfigMixin, ABC):
	defaults: FileConfig
	current_project: Final[str]
	project_unique_name: Final[str]

	def __init__(self, path: str, defaults: FileConfig, rule_set: Optional[RuleSet] = None) -> None:
		if not isfile(path):
			abort(f"Not found {basename(path)!r}, are you sure that selected project exists?")
		RuleSetConfig.__init__(self)
		self.current_project = dirname(abspath(path))
		FileConfig.__init__(self, path, defaults, raise_non_existing=True)
		RuleSetHolder.__init__(self, rule_set=rule_set)
		from .output_directory import unique_folder_name
		self.project_unique_name = unique_folder_name(self.directory)

	def get_build_path(self, *components: str) -> str:
		from .output_directory import get_temporary_directory
		return join(get_temporary_directory(), "build", self.project_unique_name, *components)

	@property
	def project_type(self) -> int:
		"""Defines project type that can be used by some sources at build time.

		Returns:
			int: one of obviously existing PROJECT_TYPEs, or something else
		"""
		return PROJECT_TYPE_UNKNOWN

	def get_project_side(self) -> Optional[str]:
		if self.get_value("info.clientOnly", False):
			return "client"
		if self.get_value("info.serverOnly", False):
			return "server"
		return None

	def get_active_side(self) -> Optional[str]:
		"""Reads the active build side from the current RuleSet properties."""
		if "client" in self.properties:
			return "client"
		if "server" in self.properties:
			return "server"
		return None

	def resolve_dependency_entry(self, dependency: Any) -> Optional[Union['MakeDataConfig', 'Artifact']]:
		"""Resolves a single dependency entry (path, int id, or dict) to a project or artifact.

		Returns None if the dependency cannot be resolved and should be skipped
		(e.g. optional and unsatisfied). Raises ValueError for required unresolved entries.
		"""
		path = None
		if isinstance(dependency, Config):
			path = dependency.get_value("path")
		elif isinstance(dependency, str):
			path = dependency

		if path and ensure_not_whitespace(path):
			absolute_path = self.get_path(path)
			project = MakeDataConfig.of(absolute_path)
			if project:
				return project
			absolute_path = GLOBALS.TOOLCHAIN_CONFIG.get_path(path)
			project = MakeDataConfig.of(absolute_path)
			if project:
				return project

		from .project_graph import Artifact
		artifact = Artifact.of(dependency)
		if artifact:
			return artifact

		if not self.get_value("project.requiredDependencies", True) or (isinstance(dependency, Config) and not dependency.get_value("required", True)):
			from .logger import attention
			attention(f"Skipping unsatisfied dependency {dependency!r}, since it is optional.")
			return None
		raise ValueError(f"Invalid dependency {dependency!r}, it should be relative project path, id or repository url!")

	def iterate_dependencies(self) -> Iterable[Union['MakeDataConfig', 'Artifact']]:
		"""Each project may contain dependencies that must be compiled before that project itself.
		When instantiating a config, toolchain will make a dependency graph first, and only then build your project.

		Returns:
			Iterable[MakeDataConfig]: optional project data on which manifest is based
		"""
		return []

	@abstractmethod
	def obtain_project_data(self) -> Optional[FlushableMakeProjectData]:
		"""Basic data describing this config and project as a whole. They should be provided in any case.
		If there is no value, no built-in startup configurations are created.

		Returns:
			Optional[FlushableMakeProjectData]: optional project data on which manifest is based
		"""
		...

	@abstractmethod
	def iterate_assets(self) -> Iterable[MakeAssetData]:
		"""Returns iterable asset data that is used in appropriate compilers and handlers.
		Assets are usually extra files, they are located in project folder and should be defined in your config.
		You are responsible for producing this data, using this config and manifests within a project.

		Returns:
			Iterable[MakeAssetData]: iterable which can be used in compilers
		"""
		return []

	@staticmethod
	def register(criteria: Union[str, Callable[[str], str]], data: Type['MakeDataConfig']) -> None:
		"""Here you can check if this project can be loaded with this config.

		Args:
			criteria (Union[str, Callable[[str], bool]]): filter configs by filename or deeper callable inspection
			data (type[MakeDataConfig]): type to be created, which will become a config with data

		Raises:
			ValueError: if this criteria has already been registered earlier
		"""
		if criteria in AVAILABLE_DATA_CONFIGS:
			raise ValueError(f"Data criteria {criteria} already occupied by {AVAILABLE_DATA_CONFIGS[criteria]}!")
		AVAILABLE_DATA_CONFIGS[criteria] = data

	@staticmethod
	def of(directory: str) -> Optional['MakeDataConfig']:
		for criteria, config_type in AVAILABLE_DATA_CONFIGS.items():
			if isinstance(criteria, str):
				config_file = join(directory, criteria)
				if not isfile(config_file):
					continue
			elif callable(criteria):
				config_file = criteria(directory)
			else:
				continue
			if not config_file:
				continue
			return config_type(config_file, defaults=GLOBALS.TOOLCHAIN_CONFIG)
