from functools import cmp_to_key
from os.path import basename, isfile, join, splitext
from typing import (Any, Iterable, MutableMapping, MutableSequence, Optional,
                    Union)

from .config import Config, FileConfig
from .language import (PROJECT_TYPE_MOD, PROJECT_TYPE_MODPACK,
                       PROJECT_TYPE_PACK, FlushableMakeProjectData,
                       MakeAssetData, MakeDataConfig, MakeJavaData,
                       MakeNativeData, MakePackGraphicsData, MakeResourceData,
                       MakeScriptData, MakeSharedObjectData)
from .project_graph import Artifact

VALID_SOURCE_TYPES = ("main", "launcher", "preloader", "instant", "custom", "library")
VALID_RESOURCE_TYPES = ("resource_directory", "gui", "minecraft_resource_pack", "minecraft_behavior_pack")

class MakeConfig(MakeDataConfig):
	def __init__(self, path: str, defaults: FileConfig) -> None:
		super().__init__(path, defaults)
		self.migrate_make_config(self)

	def migrate_make_config(self, config: FileConfig, save_then: bool = True) -> bool:
		changes = False
		if "global" in config:
			global_config = config.get_value("global", dict())
			for entry in global_config:
				config.set_value(entry, global_config[entry])
			config.delete_value("global")
			changes |= True
		if "make" in config:
			make_config = config.get_value("make", dict())
			if "linkNative" in make_config:
				config.set_value("linkNative", make_config["linkNative"])
			if "excludeFromRelease" in make_config:
				config.set_value("excludeFromRelease", make_config["excludeFromRelease"])
			config.delete_value("make")
			changes |= True
		if "gradle" in config:
			gradle_config = config.get_value("gradle", dict())
			java_config = config.get_value("java", dict())
			for entry in gradle_config:
				java_config.set_value(entry, gradle_config[entry])
			config.set_value("java", java_config)
			config.delete_value("gradle")
			changes |= True
		if save_then and changes:
			config.save_as_file()
		return changes

	def update_properties(self) -> None:
		configurations = self.get_value("configurations")
		if not isinstance(configurations, MutableMapping):
			self.overrides = None
			return
		overrides = None
		for value_set, configuration in configurations.items():
			if not self.is_relevant_configuration(value_set) or not isinstance(configuration, MutableMapping):
				continue
			if overrides is None:
				overrides = Config()
			overrides.merge_config(configuration)
		self.overrides = overrides

	@property
	def project_type(self) -> int:
		if "manifest" in self:
			return PROJECT_TYPE_PACK
		if "modpack" in self:
			return PROJECT_TYPE_MODPACK
		return PROJECT_TYPE_MOD

	def iterate_dependencies(self) -> Iterable[Union[MakeDataConfig, Artifact]]:
		resolved_dependencies = self.obtain_list("dependencies")

		# Append dependencies declared inside matching configuration blocks
		# e.g. configurations.client.dependencies / configurations.server.dependencies
		configurations = self.get_value("configurations")
		if isinstance(configurations, MutableMapping):
			for value_set, configuration in configurations.items():
				if not self.is_relevant_configuration(value_set):
					continue
				if not isinstance(configuration, MutableMapping):
					continue
				config_dependencies = configuration.get("dependencies", [])
				if isinstance(config_dependencies, list):
					resolved_dependencies = resolved_dependencies + config_dependencies

		active_side = self.get_active_side()
		for dependency in resolved_dependencies:
			resolved = self.resolve_dependency_entry(dependency)
			if resolved is None:
				continue
			if active_side and isinstance(resolved, MakeDataConfig):
				if not resolved.is_side_compatible(active_side):
					continue
			yield resolved

	def obtain_project_data(self) -> Optional[FlushableMakeProjectData]:
		if self.project_type == PROJECT_TYPE_MOD and "info" in self:
			mod_info = self.obtain_config("info")
			return self.obtain_mod_data(mod_info)
		if self.project_type == PROJECT_TYPE_MODPACK and "modpack" in self:
			modpack_relative_path = self.get_value("modpack")
			modpack_path = self.get_relative_path(modpack_relative_path)
			modpack = FileConfig(modpack_path, raise_non_existing=True)
			return self.obtain_modpack_data(modpack)
		if self.project_type == PROJECT_TYPE_PACK and "manifest" in self:
			manifest_relative_path = self.get_value("manifest")
			manifest_path = self.get_relative_path(manifest_relative_path)
			manifest = FileConfig(manifest_path, raise_non_existing=True)
			return self.obtain_pack_data(manifest)

	@property
	def supports_scripts(self) -> bool:
		return self.project_type == PROJECT_TYPE_MOD

	def iterate_scripts(self) -> Iterable[MakeScriptData]:
		sources_list = filter(
			lambda source: isinstance(source, Config),
			self.obtain_list("sources")
		)
		for source in sorted(sources_list, key=cmp_to_key(
			lambda a, b: \
				0 if a.get_value("type") == "library" and b.get_value("type") == "library" \
					else -1 if a.get_value("type") == "library" else 1
		)):
			yield self.obtain_script_data(source)

	def obtain_script_data(self, source: Config) -> MakeScriptData:
		relative_path = source.get_value_unsafe("source")
		type = source.get_value_unsafe("type")
		if not type in VALID_SOURCE_TYPES:
			raise ValueError(f"Script {relative_path!r} has invalid type, it should be one of: {', '.join(VALID_SOURCE_TYPES)}!")
		language = source.get_value("language")
		if language and language not in ("javascript", "typescript"):
			raise ValueError(f"Script {relative_path!r} has invalid language, it should be 'javascript' or 'typescript'!")

		# Using template <sourceName>.<extension> -> <sourceName>, e.g. main.js -> main
		output_path = source.get_value("target")
		if not output_path:
			script_path = self.get_relative_path(relative_path)
			output_path = basename(script_path)
			if isfile(script_path):
				output_path = splitext(output_path)[0] + ".js"

		return MakeScriptData(
			relative_path=relative_path,
			output_path=output_path,
			type=type,
			language=language,
			source_name=source.get_value("sourceName"),
			api=source.get_value("api"),
			includes_path=source.get_value("includes"),
			optimization_level=source.get_value("optimizationLevel", -1)
		)

	@property
	def supports_java(self) -> bool:
		return self.project_type in (PROJECT_TYPE_MOD, PROJECT_TYPE_PACK)

	def iterate_java(self, defaults: Optional[Config] = None) -> Iterable[MakeJavaData]:
		java_config = self.get_value("java")
		# Obtain properties from deprecated `gradle` config.
		if not isinstance(java_config, Config):
			java_config = self.obtain_config("gradle")
		if isinstance(defaults, Config):
			java_config.merge_config(defaults, exclusive_lists=True)

		from .language import get_language_directories
		directories = get_language_directories("java", java_config, make_config=self)
		for directory, config in directories.items():
			yield self.obtain_java_data(config)

	def obtain_java_data(self, config: Config) -> MakeJavaData:
		relative_path = config.get_value_unsafe("directory")
		directory = self.get_path(relative_path)
		output_path = basename(directory)

		manifest_path = join(directory, "manifest")
		manifest = FileConfig(manifest_path)
		manifest.delete_value("directory")
		config.merge_config(manifest, exclusive_lists=True)

		return self.obtain_java_manifest_data(relative_path=relative_path, output_path=output_path, config=config)

	@property
	def supports_native(self) -> bool:
		return self.project_type in (PROJECT_TYPE_MOD, PROJECT_TYPE_PACK)

	def iterate_native(self, defaults: Optional[Config] = None) -> Iterable[MakeNativeData]:
		native_config = self.obtain_config("native")
		# Obtain deprecated config `linkNative` property.
		if not "native" in self and "linkNative" in self:
			native_config.set_value("link", self.get_value("linkNative"))
		if isinstance(defaults, Config):
			native_config.merge_config(defaults, exclusive_lists=True)

		from .language import get_language_directories
		directories = get_language_directories("native", native_config, make_config=self)
		for directory, config in directories.items():
			yield self.obtain_native_data(config)

	def obtain_native_data(self, config: Config) -> MakeNativeData:
		relative_path = config.get_value_unsafe("directory")
		directory = self.get_path(relative_path)
		output_path = basename(directory)

		manifest_path = join(directory, "manifest")
		manifest = FileConfig(manifest_path)
		manifest.delete_value("directory")
		config.merge_config(manifest, exclusive_lists=True)

		return self.obtain_native_manifest_data(relative_path=relative_path, output_path=output_path, config=config)

	@property
	def supports_shared_objects(self) -> bool:
		return self.project_type == PROJECT_TYPE_PACK

	def iterate_shared_objects(self) -> Iterable[MakeSharedObjectData]:
		for shared_object in self.obtain_list("native.sharedObjects"):
			if not isinstance(shared_object, str):
				continue
			yield self.obtain_shared_object_data(shared_object)

	def obtain_shared_object_data(self, shared_object: str) -> MakeSharedObjectData:
		formatted_shared_object = shared_object.format("")
		if shared_object == formatted_shared_object:
			if not shared_object[-1] == "*":
				raise ValueError(f"Shared object path {formatted_shared_object!r} should contain required architecture or ends with asterisk!")
			shared_object = shared_object[0:-1] + "/{}/*"

		return MakeSharedObjectData(
			relative_path=shared_object
		)

	@property
	def supports_resources(self) -> bool:
		return self.project_type in (PROJECT_TYPE_MOD, PROJECT_TYPE_MODPACK)

	def iterate_resources(self) -> Iterable[MakeResourceData]:
		for source in self.obtain_list("resources"):
			if not isinstance(source, Config):
				continue
			yield self.obtain_resource_data(source)

	def obtain_resource_data(self, source: Config) -> MakeResourceData:
		relative_path = source.get_value_unsafe("path")
		type = source.get_value_unsafe("type")
		if not type in VALID_RESOURCE_TYPES:
			raise ValueError(f"Resource {relative_path!r} has invalid type, it should be one of: {', '.join(VALID_RESOURCE_TYPES)}!")

		return MakeResourceData(
			relative_path=relative_path,
			output_path=basename(relative_path),
			type=type,
			push_unchanged_files=source.get_value("pushUnchangedFiles"),
			cleanup_remote=source.get_value("cleanupRemote")
		)

	@property
	def supports_pack_graphics(self) -> bool:
		return self.project_type == PROJECT_TYPE_PACK

	def iterate_pack_graphics(self) -> Iterable[MakePackGraphicsData]:
		graphics_groups = self.obtain_config("pack.graphics")
		for group_name, images in graphics_groups.items():
			yield self.obtain_pack_graphics_data(group_name, images)

	def obtain_pack_graphics_data(self, group_name: str, data: Any) -> MakePackGraphicsData:
		if isinstance(data, str):
			data = [data]
		if not isinstance(data, MutableSequence):
			raise ValueError(f"Pack graphics group {group_name!r} should contain graphic directory or list of them, got: {data}!")

		return MakePackGraphicsData(
			group_name=group_name,
			images=data
		)

	def iterate_assets(self) -> Iterable[MakeAssetData]:
		for source in self.obtain_list("additional"):
			if isinstance(source, str):
				yield MakeAssetData(
					relative_path=source,
					output_path="."
				)
				continue
			if not isinstance(source, Config):
				continue
			yield self.obtain_asset_data(source)

	def obtain_asset_data(self, source: Config) -> MakeAssetData:
		relative_path = source.get_value("source", lambda: source.get_value_unsafe("path"))
		output_path = source.get_value("targetDir", ".")

		return MakeAssetData(
			relative_path=relative_path,
			output_path=output_path,
			output_filename=source.get_value("targetFile"),
			push_unchanged_files=source.get_value("pushUnchangedFiles"),
			cleanup_remote=source.get_value("cleanupRemote")
		)
