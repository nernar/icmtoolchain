import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from os.path import abspath, isfile, join
from typing import Dict, List, Optional

from .config import FileConfig
from .hglob import glob
from .language import MakeDataConfig
from .utils import ensure_directory, is_empty_directory, remove_tree


@dataclass
class BuildTargetEntry:
	name: str
	relative_path: str
	absolute_path: str
	exclude: bool = False
	declare: Dict = field(default_factory=dict)
	declare_default: Dict = field(default_factory=dict)

class BuildTarget:
	def __init__(self, relative_directory: str, output_directory: str, export_group: str = ""):
		self.relative_directory = relative_directory
		self.output_directory = output_directory
		self.export_group = export_group
		self.entries: List[BuildTargetEntry] = []

	def declare(self, relative_path: str, exclude: bool = False, declare: Optional[Dict] = None, declare_default: Optional[Dict] = None) -> BuildTargetEntry:
		target_relative_path = join(self.relative_directory, relative_path)
		if "{}" not in target_relative_path:
			target_relative_path += "{}"

		existing_names = { entry.name for entry in self.entries }
		index = 2
		formatted_name = target_relative_path.format("")
		while formatted_name in existing_names:
			formatted_name = target_relative_path.format(index)
			index += 1

		entry = BuildTargetEntry(
			name=formatted_name,
			relative_path=formatted_name.replace("\\", "/"),
			absolute_path=join(self.output_directory, formatted_name),
			exclude=exclude,
			declare=declare or {},
			declare_default=declare_default or {}
		)
		self.entries.append(entry)
		return entry

	def cleanup(self, clear_output: bool = True) -> None:
		ensure_directory(self.output_directory)

	def cleanup_after_export(self) -> None:
		if is_empty_directory(self.output_directory, non_existing_as_empty=False):
			remove_tree(self.output_directory)

	def export_entry(self, entry: BuildTargetEntry) -> Optional[Dict]:
		return {
			"path": entry.relative_path,
			**entry.declare
		}

	def export(self, default_overrides: Optional[Dict] = None) -> List[Dict]:
		result = []
		for entry in self.entries:
			if entry.exclude:
				continue
			entry_result = self.export_entry(entry)
			if not entry_result:
				continue
			result.append(entry_result)
			if default_overrides is not None and entry.declare_default:
				default_overrides.update(entry.declare_default)
		return result

class IsolatedTarget(BuildTarget):
	def cleanup(self, clear_output: bool = True) -> None:
		self.entries.clear()
		if clear_output: # XXX: GLOBALS.PREFERRED_CONFIG.get_value("development.clearOutput")
			remove_tree(self.output_directory)
		super().cleanup(clear_output=clear_output)

class ResourceIsolatedTarget(IsolatedTarget):
	def __init__(self, relative_directory: str, output_directory: str, resource_type: str = "resource", export_group: str = ""):
		self.resource_type = resource_type
		super().__init__(relative_directory=relative_directory, output_directory=output_directory, export_group=export_group)

	def export_entry(self, entry: BuildTargetEntry) -> Optional[Dict]:
		return {
			"resourceType": self.resource_type,
			"path": entry.relative_path,
			**entry.declare
		}

class InplaceTarget(BuildTarget):
	def __init__(self, relative_directory: str, output_directory: str, allow_cleanup: bool = False, export_group: str = ""):
		self.allow_cleanup = allow_cleanup
		super().__init__(relative_directory=relative_directory, output_directory=output_directory, export_group=export_group)

	def cleanup(self, clear_output: bool = True) -> None:
		if clear_output and self.allow_cleanup:
			for entry in self.entries:
				self.cleanup_entry(entry)
		self.entries.clear()
		super().cleanup(clear_output=clear_output)

	def cleanup_entry(self, entry: BuildTargetEntry) -> None:
		entry_directory = join(self.output_directory, entry.relative_path)
		if isfile(entry_directory):
			os.remove(entry_directory)

class ResourceInplaceTarget(InplaceTarget):
	def __init__(self, relative_directory: str, output_directory: str, allow_cleanup: bool = False, resource_type: str = "resource", export_group: str = ""):
		self.resource_type = resource_type
		super().__init__(relative_directory=relative_directory, output_directory=output_directory, allow_cleanup=allow_cleanup, export_group=export_group)

	def export_entry(self, entry: BuildTargetEntry) -> Optional[Dict]:
		return {
			"resourceType": self.resource_type,
			"path": entry.relative_path,
			**entry.declare
		}

class JavaInplaceTarget(InplaceTarget):
	def cleanup_entry(self, entry: BuildTargetEntry) -> None:
		entry_directory = join(self.output_directory, entry.relative_path)
		for classes_dex in glob(join(entry_directory, "classes*.*dex")):
			if isfile(classes_dex):
				os.remove(classes_dex)

class NativeInplaceTarget(InplaceTarget):
	def cleanup_entry(self, entry: BuildTargetEntry) -> None:
		entry_directory = join(self.output_directory, entry.relative_path)
		for library_executable in glob(join(entry_directory, "lib*.so")):
			if isfile(library_executable):
				os.remove(library_executable)

class ProjectStructure(ABC):
	def __init__(self, output_directory: str):
		self.directory = output_directory
		self.targets: Dict[str, BuildTarget] = {}
		self.setup_targets()

	def append(self, keyword: str, target: BuildTarget) -> BuildTarget:
		if keyword in self.targets:
			raise ValueError(f"Project target {keyword!r} already used within structure!")
		self.targets[keyword] = target
		return target

	def get(self, keyword: str) -> BuildTarget:
		if not keyword in self.targets:
			raise KeyError(f"Project target {keyword!r} cannot be satisfied!")
		return self.targets[keyword]

	def declare_target(self, keyword: str, relative_path: str, exclude: bool = False, declare: Optional[Dict] = None, declare_default: Optional[Dict] = None) -> BuildTargetEntry:
		return self.get(keyword).declare(relative_path=relative_path, exclude=exclude, declare=declare, declare_default=declare_default)

	@abstractmethod
	def setup_targets(self) -> None: ...

	@abstractmethod
	def generate_config(self) -> None: ...

	def export_targets(self, group: str, default_overrides: Optional[Dict] = None) -> List[Dict]:
		result = []
		for target in self.targets.values():
			if target.export_group == group:
				result.extend(target.export(default_overrides))
		return result

	def cleanup_target(self, keyword: str, clear_output: bool = True) -> None:
		self.get(keyword).cleanup(clear_output=clear_output)

	def cleanup(self, clear_output: bool = True) -> None:
		for target in self.targets.values():
			target.cleanup(clear_output)

	def cleanup_after_export(self) -> None:
		for target in self.targets.values():
			target.cleanup_after_export()

class BuildConfigStructure(ProjectStructure, ABC):
	def __init__(self, output_directory: str, make_config: MakeDataConfig):
		self.make_config = make_config
		super().__init__(output_directory)

	@classmethod
	def resolve_strategy(cls, project_directory: str, output_directory: str, make_config: MakeDataConfig) -> ProjectStructure:
		project_path = abspath(project_directory)
		output_path = abspath(output_directory)
		default_output_path = abspath(join(project_directory, "output"))

		if project_path == output_path:
			return InplaceBuildConfigStructure(output_path, make_config)

		if output_path == default_output_path:
			return IsolatedBuildConfigStructure(output_path, make_config)

		config_path = join(output_path, "build.config")
		if isfile(config_path):
			return InplaceBuildConfigStructure(output_path, make_config)

		return IsolatedBuildConfigStructure(output_path, make_config)

	def merge_target_group(self, config: Dict, group_name: str, generated_items: List[Dict]) -> None:
		existing_items = config.get(group_name, [])
		if not isinstance(existing_items, list):
			existing_items = []

		existing_paths = { item.get("path") for item in existing_items if isinstance(item, dict) and "path" in item }

		for generated_item in generated_items:
			if generated_item["path"] not in existing_paths:
				existing_items.append(generated_item)

		config[group_name] = existing_items

	def generate_config(self) -> None:
		config_path = join(self.directory, "build.config")
		config = FileConfig(config_path)

		if not "defaultConfig" in config:
			config.set_value("defaultConfig", {
				"readme": "this build config is generated automatically by icmtoolchain",
				"buildType": "develop"
			})
			if not "buildDirs" in config:
				config.set_value("buildDirs", [])
		default_config = config.get_value("defaultConfig")

		self.merge_target_group(config, "compile", self.export_targets("compile", default_config))
		self.merge_target_group(config, "resources", self.export_targets("resources"))
		self.merge_target_group(config, "javaDirs", self.export_targets("javaDirs"))
		self.merge_target_group(config, "nativeDirs", self.export_targets("nativeDirs"))

		config.save_as_file(indent=" " * 2)
		self.cleanup_after_export()

class IsolatedBuildConfigStructure(BuildConfigStructure):
	def setup_targets(self) -> None:
		resource_path = self.make_config.get_value("target.resource_directory", "resources")
		self.append("resources", ResourceIsolatedTarget(resource_path, join(self.directory, resource_path), export_group="resources"))
		gui_path = self.make_config.get_value("target.gui", "gui")
		self.append("gui", ResourceIsolatedTarget(gui_path, join(self.directory, gui_path), resource_type="gui", export_group="resources"))

		script_path = self.make_config.get_value("target.source", "source")
		self.append("scripts", IsolatedTarget(script_path, join(self.directory, script_path), export_group="compile"))
		library_path = self.make_config.get_value("target.library", "library")
		self.append("libraries", IsolatedTarget(library_path, join(self.directory, library_path), export_group="compile"))

		java_path = self.make_config.get_value("target.java", "java")
		self.append("java", IsolatedTarget(java_path, join(self.directory, java_path), export_group="javaDirs"))
		native_path = self.make_config.get_value("target.native", "native")
		self.append("native", IsolatedTarget(native_path, join(self.directory, native_path), export_group="nativeDirs"))
		shared_object_path = self.make_config.get_value("target.shared_object", "so")
		self.append("shared_objects", IsolatedTarget(shared_object_path, join(self.directory, shared_object_path), export_group="sharedObjects"))

		resource_packs_path = self.make_config.get_value("target.minecraft_resource_pack", "resource_packs")
		self.append("resource_packs", IsolatedTarget(resource_packs_path, join(self.directory, resource_packs_path)))
		behavior_packs_path = self.make_config.get_value("target.minecraft_behavior_pack", "behavior_packs")
		self.append("behavior_packs", IsolatedTarget(behavior_packs_path, join(self.directory, behavior_packs_path)))

class InplaceBuildConfigStructure(BuildConfigStructure):
	def setup_targets(self) -> None:
		resource_path = self.make_config.get_value("target.resource_directory", "resources")
		self.append("resources", ResourceInplaceTarget(resource_path, join(self.directory, resource_path), export_group="resources"))
		gui_path = self.make_config.get_value("target.gui", "gui")
		self.append("gui", ResourceInplaceTarget(gui_path, join(self.directory, gui_path), resource_type="gui", export_group="resources"))

		script_path = self.make_config.get_value("target.source", "source")
		self.append("scripts", InplaceTarget(script_path, join(self.directory, script_path), export_group="compile"))
		library_path = self.make_config.get_value("target.library", "library")
		self.append("libraries", InplaceTarget(library_path, join(self.directory, library_path), export_group="compile"))

		java_path = self.make_config.get_value("target.java", "java")
		self.append("java", JavaInplaceTarget(java_path, join(self.directory, java_path), allow_cleanup=True, export_group="javaDirs"))
		native_path = self.make_config.get_value("target.native", "native")
		self.append("native", NativeInplaceTarget(native_path, join(self.directory, native_path), allow_cleanup=True, export_group="nativeDirs"))
		shared_object_path = self.make_config.get_value("target.shared_object", "so")
		self.append("shared_objects", InplaceTarget(shared_object_path, join(self.directory, shared_object_path), export_group="sharedObjects"))

		resource_packs_path = self.make_config.get_value("target.minecraft_resource_pack", "resource_packs")
		self.append("resource_packs", InplaceTarget(resource_packs_path, join(self.directory, resource_packs_path)))
		behavior_packs_path = self.make_config.get_value("target.minecraft_behavior_pack", "behavior_packs")
		self.append("behavior_packs", InplaceTarget(behavior_packs_path, join(self.directory, behavior_packs_path)))
