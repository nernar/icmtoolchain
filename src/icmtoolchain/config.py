from itertools import chain
from json import JSONDecodeError
from json import dump as dump_json
from json import load as load_json
from os.path import (abspath, basename, dirname, exists, isfile, join,
                     normpath, relpath)
from typing import (Any, Callable, Dict, Iterable, List, MutableMapping,
                    MutableSequence, Optional, Protocol, Type, TypeVar, Union,
                    cast)


class ConfigSupportsKeysAndGetItem(Protocol):
    def keys(self) -> Iterable[str]: ...
    def __getitem__(self, key: str, /) -> Any: ...

ConfigResultType = TypeVar("ConfigResultType")

class Config(Dict[str, Any]):
	@property
	def defaults(self) -> Optional['Config']:
		return self._defaults

	@defaults.setter
	def defaults(self, value: Optional['Config']) -> None:
		if value is self:
			raise ValueError("Config defaults cannot point to config itself!")
		self._defaults = value

	def __init__(self, map: Optional[ConfigSupportsKeysAndGetItem] = None, defaults: Optional['Config'] = None):
		super().__init__()
		self.defaults = defaults
		if map is not None:
			self.update(map)

	def __hash__(self):
		return hash(frozenset(self))

	def is_supported_value(self, value: Any) -> bool:
		return value is None \
			or isinstance(value, int) \
			or isinstance(value, float) \
			or isinstance(value, bool) \
			or isinstance(value, str) \
			or isinstance(value, MutableSequence) \
			or isinstance(value, MutableMapping)

	def get_dict_value(self, key: str) -> Any:
		return super().__getitem__(key)

	def get_value(self, key: str, fallback: Any = None, *, allow_prototype: bool = True) -> Any:
		try:
			return self.get_value_unsafe(key)
		except KeyError:
			if allow_prototype and self.defaults is not None:
				return self.defaults.get_value(key, fallback)
		return fallback() if callable(fallback) else fallback

	def get_value_unsafe(self, key: str) -> Any:
		if not "." in key:
			return self.get_dict_value(key)

		namespace_keys = key.partition(".")
		namespace = self.get_dict_value(namespace_keys[0])
		if not isinstance(namespace, Config):
			raise KeyError(key)

		return namespace.get_value_unsafe(namespace_keys[2])

	def __getitem__(self, key: str, /) -> Any:
		return self.get_value_unsafe(key)

	def obtain(
		self,
		key: str,
		result_type: Type[ConfigResultType],
		fallback: Optional[Union[ConfigResultType, Callable[[], ConfigResultType]]] = None,
		*,
		implace_fallback: bool = True,
		allow_prototype: bool = True
	) -> ConfigResultType:
		value = self.get_value(key, allow_prototype=allow_prototype)
		if isinstance(value, result_type):
			return value
		prototype_value = None
		if self.defaults is not None:
			prototype_value = self.defaults.get_value(key)
		value = self.replace_value(value, prototype_value)
		requires_implace = isinstance(value, result_type)
		if not requires_implace:
			if fallback is not None:
				value = cast(ConfigResultType, fallback() if callable(fallback) else fallback)
			else:
				value = result_type()
		if requires_implace or implace_fallback:
			self.set_value(key, value)
		return value

	def obtain_config(self, key: str, *, implace_fallback: bool = False, allow_prototype: bool = True) -> 'Config':
		config = self.obtain(key, Config, implace_fallback=implace_fallback, allow_prototype=False)
		if allow_prototype and self.defaults is not None:
			# Ensure that fallbacks/merge strategies are passed to subconfigs.
			prototype_config = self.defaults.obtain(key, Config, implace_fallback=False, fallback=lambda: None)
			if prototype_config and prototype_config != config.defaults:
				if config.defaults is not None:
					prototype_config.defaults = config.defaults
				config.defaults = prototype_config
		return config

	def obtain_list(self, key: str, *, implace_fallback: bool = False, allow_prototype: bool = True) -> List:
		sequence = self.obtain(key, list, fallback=lambda: list(), implace_fallback=implace_fallback, allow_prototype=False)
		if allow_prototype and self.defaults is not None:
			# Merging lists, maybe creating a new one is sometimes not convenient, then you should use allow_prototype=False.
			prototype_sequence = self.defaults.obtain_list(key, implace_fallback=False)
			if len(prototype_sequence) > 0:
				return list(chain(sequence, prototype_sequence))
		return sequence

	def update_values(self, map: ConfigSupportsKeysAndGetItem, *, replace_mismatched_types: bool = False, strip_none_from_lists: bool = False) -> None:
		for key in map.keys():
			self.set_value_unsafe(key, map[key], replace_mismatched_types=replace_mismatched_types, strip_none_from_lists=strip_none_from_lists)

	def update(self, map: ConfigSupportsKeysAndGetItem) -> None:
		self.update_values(map, replace_mismatched_types=True)

	def set_dict_value(self, key: str, value: Any) -> None:
		super().__setitem__(key, value)

	def set_value(self, key: str, value: Any, strip_none_from_lists: bool = False) -> None:
		self.set_value_unsafe(key, value, replace_mismatched_types=True, strip_none_from_lists=strip_none_from_lists)

	def set_value_unsafe(self, key: str, value: Any, *, replace_mismatched_types: bool = False, strip_none_from_lists: bool = False):
		if not self.is_supported_value(value):
			raise ValueError(f"Config value should be primitive, array or nested config, got {key!r}: {type(value)}!")
		if not "." in key:
			fallback = None
			if self.defaults is not None:
				fallback = self.defaults.get_value(key)
			self.set_dict_value(key, self.replace_value(value, fallback, strip_none_from_lists=strip_none_from_lists))
			return

		namespace_keys = key.partition(".")
		namespace = self.get_value(namespace_keys[0])
		if not isinstance(namespace, Config):
			if namespace is not None and not replace_mismatched_types:
				raise ValueError(f"{key!r}: {namespace}")
			namespace = Config()
			if self.defaults is not None:
				namespace.defaults=self.defaults.obtain_config(key, implace_fallback=False)
			self.set_dict_value(namespace_keys[0], namespace)

		namespace.set_value_unsafe(namespace_keys[2], value, replace_mismatched_types=replace_mismatched_types)

	def replace_value(self, obj: Any, fallback: Any = None, strip_none_from_lists: bool = False) -> Any:
		if not self.is_supported_value(obj):
			return None
		if isinstance(obj, MutableMapping) and not isinstance(obj, Config):
			obj = Config(obj)
		if isinstance(obj, MutableSequence):
			for offset, value in enumerate(obj):
				obj[offset] = self.replace_value(value, strip_none_from_lists=strip_none_from_lists)
			if strip_none_from_lists:
				while None in obj:
					obj.remove(None)
		if isinstance(obj, Config) and self.defaults is not None and isinstance(fallback, Config):
			obj.defaults = fallback
		return obj

	def __setitem__(self, key: str, value: Any, /) -> None:
		self.set_value_unsafe(key, value, replace_mismatched_types=True)

	def merge_config(self, config: Union[MutableMapping, 'Config'], *, replace_configs: bool = False, exclusive_lists: bool = False, extend_lists: bool = False, strip_none_from_lists: bool = False) -> None:
		if exclusive_lists:
			extend_lists = True

		for key, value in config.items():
			fallback = None
			if self.defaults is not None:
				fallback = self.defaults.get_value(key)
			if not key in self:
				self.set_dict_value(key, self.replace_value(value, fallback, strip_none_from_lists=strip_none_from_lists))
				continue

			current_value = self.get_dict_value(key)
			if not replace_configs and isinstance(value, Config) and isinstance(current_value, Config):
				current_value.merge_config(value, extend_lists=extend_lists, strip_none_from_lists=strip_none_from_lists)
				continue
			if extend_lists and isinstance(value, MutableSequence) and isinstance(current_value, MutableSequence):
				if not exclusive_lists:
					current_value.extend(value)
				else:
					for obj in filter(lambda obj: obj not in current_value, value):
						current_value.append(obj)
				continue

			self.set_dict_value(key, self.replace_value(value, fallback, strip_none_from_lists=strip_none_from_lists))

	def delete_dict_value(self, key: str) -> None:
		if key in self:
			super().__delitem__(key)

	def delete_value(self, key: str, *, remove_when_empty: bool = True) -> None:
		self.delete_value_unsafe(key, remove_mismatched_types=True, remove_when_empty=remove_when_empty)

	def delete_value_unsafe(self, key: str, *, remove_mismatched_types: bool = False, remove_when_empty: bool = True) -> None:
		if not "." in key:
			self.delete_dict_value(key)
			return

		namespace_keys = key.partition(".")
		namespace = self.get_dict_value(namespace_keys[0])
		if not isinstance(namespace, Config):
			if remove_mismatched_types:
				self.delete_dict_value(key)
				return
			raise ValueError(f"{key!r}: {namespace}")

		namespace.delete_value_unsafe(namespace_keys[2], remove_mismatched_types=remove_mismatched_types, remove_when_empty=remove_when_empty)
		if remove_when_empty:
			for _ in namespace:
				return
			self.delete_dict_value(key)

	def __delitem__(self, key: str, /) -> None:
		self.delete_value_unsafe(key, remove_mismatched_types=True)

	def as_json(self, *, strip_none: bool = True) -> MutableMapping:
		json = {}
		for key, value in self.items():
			if not isinstance(value, Config):
				continue
			json[key] = value.as_json(strip_none=strip_none)
		for key, value in self.items():
			if isinstance(value, Config) or (
				strip_none and value is None
			):
				continue
			if isinstance(value, MutableSequence):
				value = [
					obj.as_json(strip_none=strip_none) if isinstance(obj, Config) \
						else obj for obj in value if not strip_none or obj is not None
				]
			json[key] = value
		return json

class FileConfig(Config):
	def __init__(self, path: str, defaults: Optional['Config'] = None, map: Optional[ConfigSupportsKeysAndGetItem] = None, *, do_not_read: bool = False, raise_non_existing: bool = False):
		super().__init__(map=map, defaults=defaults)
		self.path = path
		self.directory = dirname(abspath(path))
		if not do_not_read:
			self.read_from_file(raise_non_existing=raise_non_existing)

	def read_from_file(self, *, raise_non_existing: bool = True, merge_with_existing: bool = False) -> None:
		non_existing = not isfile(self.path)
		if raise_non_existing and non_existing:
			raise ValueError(f"{self.path} does not exist!")
		if not merge_with_existing:
			self.clear()
		if non_existing:
			return

		with open(self.path, encoding="utf-8") as file:
			try:
				config = load_json(file)
				if not merge_with_existing:
					self.update_values(config, replace_mismatched_types=True)
					return
				self.merge_config(Config(config))
			except JSONDecodeError as exc:
				raise ValueError(f"Malformed {basename(self.path)!r}, you should fix it! {exc.msg}")

	def save_as_file(self, output_path: Optional[str] = None, indent: Optional[Union[int, str]] = None) -> None:
		path_to_save = output_path or self.path
		if not isfile(path_to_save):
			from .utils import ensure_file
			ensure_file(path_to_save)

		with open(path_to_save, "w", encoding="utf-8") as file:
			try:
				dump_json(self.as_json(), file, indent=indent, ensure_ascii=False)
				file.write("\n")
			except TypeError as exc:
				raise ValueError(f"Malformed config {path_to_save!r} due to internal error! {exc}")

	def get_relative_path(self, path_from_config: str) -> str:
		return abspath(join(self.directory, normpath(path_from_config)))

	def get_path(self, path_from_config: str) -> str:
		relative_path = self.get_relative_path(path_from_config)
		absolute_path = abspath(path_from_config)
		return absolute_path if not exists(relative_path) and exists(absolute_path) else relative_path

	def get_path_to_config(self, path_from_config: str) -> str:
		absolute_path = self.get_path(path_from_config)
		return relpath(absolute_path, self.directory)
