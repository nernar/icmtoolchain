from abc import ABC, abstractmethod
from functools import cmp_to_key
from itertools import chain
from os.path import basename
from typing import (Any, Callable, Dict, MutableSequence, MutableSet, Optional,
                    Tuple, Type, Union)

from .config import FileConfig
from .language import MakeDataConfig

AVAILABLE_ARTIFACTS: Dict[Union[type, Callable[[Any], bool]], Union[Callable[[Any], 'Artifact'], Type['Artifact']]] = {}

class Artifact(ABC):
	def __init__(self, description: Any) -> None:
		self.description = description

	@abstractmethod
	def fetch(self) -> None:
		...

	@abstractmethod
	def as_project(self) -> Optional['MakeDataConfig']:
		...

	@staticmethod
	def register(criteria: Union[type, Callable[[Any], bool]], data: Union[Callable[[Any], 'Artifact'], Type['Artifact']]) -> None:
		"""Here you can match right artifact to different types of dependencies.

		Args:
			criteria (Union[str, Callable[[str], bool]]): filter artifact types by filename or deeper callable inspection
			data (type[Artifact]): type to be created, which will become a artifact with data

		Raises:
			ValueError: if this criteria has already been registered earlier
		"""
		if criteria in AVAILABLE_ARTIFACTS:
			raise ValueError(f"Data criteria {criteria} already occupied by {AVAILABLE_ARTIFACTS[criteria]}!")
		AVAILABLE_ARTIFACTS[criteria] = data

	@staticmethod
	def of(description: Any) -> Optional['Artifact']:
		for criteria, artifact_type in AVAILABLE_ARTIFACTS.items():
			if isinstance(criteria, type):
				if not isinstance(description, criteria):
					continue
			elif callable(criteria):
				if not criteria(description):
					continue
			else:
				continue
			return artifact_type(description)

class ModBrowserArtifact(Artifact):
	description: int

	def __init__(self, remote_id: int) -> None:
		super().__init__(remote_id)

	def fetch(self) -> None:
		pass

	def as_project(self) -> Optional['MakeDataConfig']:
		raise NotImplementedError()

Artifact.register(int, ModBrowserArtifact)
Artifact.register(lambda description: isinstance(description, dict) and "projectId" in description, lambda description: ModBrowserArtifact(description["projectId"]))

class RepositoryArtifact(Artifact):
	description: str

	def __init__(self, remote_url: str) -> None:
		super().__init__(remote_url)

	def fetch(self) -> None:
		pass

	def as_project(self) -> Optional['MakeDataConfig']:
		raise NotImplementedError()

Artifact.register(lambda description: isinstance(description, str) and "://" in description, RepositoryArtifact)
Artifact.register(lambda description: isinstance(description, dict) and "url" in description, lambda description: RepositoryArtifact(description["url"]))

class ProjectEdge:
	dependencies: MutableSequence['ProjectEdge']
	references: MutableSequence['ProjectEdge']
	artifact: Optional[Artifact] = None

	def __init__(self, project: Union[MakeDataConfig, Artifact]):
		self.project = project
		if isinstance(project, Artifact):
			self.artifact = project
		self.dependencies = []
		self.references = []

	def __repr__(self) -> str:
		return f"ProjectEdge(project={self.project}, dependencies=({', '.join(str(edge.project) for edge in self.dependencies)}), references=({', '.join(str(edge.project) for edge in self.references)}))"

	def __str__(self) -> str:
		opt_suffix = f" ({len(self.dependencies)} dependencies)" if len(self.dependencies) else ""
		return f"Project {basename(self.project.directory) if isinstance(self.project, FileConfig) else self.project}{opt_suffix}"

class ProjectGraph(Dict[Union[MakeDataConfig, Artifact], ProjectEdge]):
	def __init__(self, project: MakeDataConfig) -> None:
		self.project = project
		self.obtain_edge(project)

	@property
	def root(self) -> ProjectEdge:
		return self.obtain_edge(self.project)

	def obtain_edge(self, config: Union[MakeDataConfig, Artifact]) -> ProjectEdge:
		if not config in self:
			self[config] = ProjectEdge(config)
		return self[config]

	def obtain_project(self, edge: ProjectEdge) -> Union[MakeDataConfig, Artifact]:
		for config, subedge in self.items():
			if edge == subedge:
				return config
		raise ValueError(f"ProjectGraph#project_of: Unresolved edge {edge.project}!")

	def depend_on(self, edge: ProjectEdge, dependency: ProjectEdge) -> None:
		if not dependency in edge.dependencies:
			edge.dependencies.append(dependency)
		if not edge in dependency.references:
			dependency.references.append(edge)

	def undepend_on(self, edge: ProjectEdge, dependency: ProjectEdge, keep_unused: bool = False) -> None:
		removed_something = False
		if edge in dependency.references:
			dependency.references.remove(edge)
			removed_something = True
		if dependency in edge.dependencies:
			edge.dependencies.remove(dependency)
			removed_something = True
		if removed_something and not keep_unused:
			self.remove_unused_edges()

	def remove_edge(self, edge: ProjectEdge, keep_unused: bool = False) -> None:
		edge_key = None
		for config, subedge in self.items():
			if edge == subedge:
				edge_key = config
				break
		if edge_key:
			del self[edge_key]
		for config, subedge in self.items():
			if edge in subedge.dependencies:
				subedge.dependencies.remove(edge)
			if edge in subedge.references:
				subedge.references.remove(edge)
		if not keep_unused:
			self.remove_unused_edges()

	def remove_unused_edges(self, edge: Optional[ProjectEdge] = None) -> MutableSet[ProjectEdge]:
		removed_edges = set()
		dependencies = self.traverse_referenced_nodes(edge)
		for config, node in list(self.items()):
			if node not in dependencies:
				removed_edges.add(node)
				del self[config]
		return removed_edges

	def collect_dependencies(self, config: MakeDataConfig, parent: Optional[ProjectEdge] = None) -> None:
		if not parent:
			parent = self.root
		for dependency in config.iterate_dependencies():
			if isinstance(dependency, MakeDataConfig) and not dependency in self:
				self.collect_dependencies(dependency, self.obtain_edge(dependency))
			self.depend_on(parent, self.obtain_edge(dependency))

	def resolve_dependencies(self, edge: Optional[ProjectEdge] = None, keep_unused: bool = False, traversed_references: Optional[MutableSet[ProjectEdge]] = None) -> MutableSet[ProjectEdge]:
		if not edge:
			edge = self.root
		edge_unresolved_artifacts: MutableSet[ProjectEdge] = set()
		unresolved_artifacts: MutableSet[ProjectEdge] = set()
		if not traversed_references:
			traversed_references = set()
		traversed_references.add(edge)
		for dependency in edge.dependencies:
			if dependency in traversed_references:
				# Unresolved dependencies will be shown and collected later,
				# we just need to avoid recursion.
				continue
			if not isinstance(dependency.project, MakeDataConfig):
				assert dependency.artifact
				dependency.artifact.fetch()
				project = dependency.artifact.as_project()
				# Ignoring unresolved projects intentionally, otherwise artifact
				# itself can throw an error if resolving required.
				if not project:
					edge_unresolved_artifacts.add(dependency)
					traversed_references.add(dependency)
					continue
				dependency.project = project
				self.collect_dependencies(project, dependency)
			dependency_unresolved_artifacts = self.resolve_dependencies(dependency, keep_unused=keep_unused, traversed_references=traversed_references)
			unresolved_artifacts.update(dependency_unresolved_artifacts)
		unresolved_artifacts.update(edge_unresolved_artifacts)
		if not keep_unused:
			for artifact_edge in edge_unresolved_artifacts:
				self.remove_edge(artifact_edge)
		return unresolved_artifacts

	def find_dependencies(self, dependency: ProjectEdge) -> MutableSequence[ProjectEdge]:
		dependencies = []
		for config, node in self.items():
			if dependency in node.dependencies:
				dependencies.append(node)
		return dependencies

	def find_references(self, reference: ProjectEdge) -> MutableSequence[ProjectEdge]:
		references = []
		for config, node in self.items():
			if reference in node.references:
				references.append(node)
		return references

	def traverse_referenced_nodes(self, edge: Optional[ProjectEdge] = None, collected_edges: Optional[MutableSet[ProjectEdge]] = None) -> MutableSet[ProjectEdge]:
		if not edge:
			edge = self.root
		if not collected_edges:
			collected_edges = set()
		collected_edges.add(edge)
		for reference in chain(edge.dependencies, edge.references):
			if reference in collected_edges:
				continue
			self.traverse_referenced_nodes(reference, collected_edges=collected_edges)
		return collected_edges
	
	def traverse_priority_dependencies(self, edge: Optional[ProjectEdge] = None) -> MutableSequence[ProjectEdge]:
		if not edge:
			edge = self.root
		def compare_dependencies(a: ProjectEdge, b: ProjectEdge) -> int:
			# Number of dependencies is prioritized, fewer is better.
			da = len(a.dependencies)
			db = len(b.dependencies)
			if da < db:
				return -1
			elif da > db:
				return 1
			# Now references, more is better.
			ra = len(a.references)
			rb = len(b.references)
			if ra > rb:
				return -1
			elif ra < rb:
				return 1
			return 0
		return sorted(self.values(), key=cmp_to_key(compare_dependencies))

	def traverse_dependencies(self, edge: Optional[ProjectEdge] = None) -> MutableSequence[ProjectEdge]:
		dependencies = self.traverse_priority_dependencies(edge)
		traversed_dependencies: MutableSequence[ProjectEdge] = []
		unresolved_dependencies: MutableSequence[ProjectEdge] = []
		index = 0
		while index < len(dependencies):
			reference = dependencies[index]
			# All previously listed dependencies must satisfy reference.
			unresolved = False
			for dependency in reference.dependencies:
				if not dependency in traversed_dependencies:
					unresolved = True
					break
			if unresolved:
				unresolved_dependencies.append(reference)
				dependencies.remove(reference)
				continue
			traversed_dependencies.append(reference)
			# Now that dependency is resolved, we can make sure that there
			# are references for following iterations.
			for dependency in reversed(unresolved_dependencies[:]):
				# If there is no reference, dependency is still unresolved.
				if reference not in dependency.dependencies:
					continue
				unresolved = False
				for requirement in dependency.dependencies:
					if requirement not in traversed_dependencies:
						unresolved = True
						break
				if not unresolved:
					dependencies.insert(index + 1, dependency)
					unresolved_dependencies.remove(dependency)
			index += 1
		return dependencies	

	def find_circular_reference(self, node: Optional[ProjectEdge] = None, visited: Optional[MutableSequence[ProjectEdge]] = None) -> Optional[Tuple[ProjectEdge, ProjectEdge]]:
		if not node:
			node = self.root
		if not visited:
			visited = list()
		if node in visited:
			return (visited[-1], node)
		visited.append(node)
		for dependency in node.dependencies:
			cross_references = self.find_circular_reference(dependency, visited)
			if cross_references:
				return cross_references
		visited.remove(node)

class ConcurrentScheduler:
	def __init__(self, graph: ProjectGraph, edge: Optional[ProjectEdge] = None) -> None:
		self.graph = graph
		self.pending: MutableSet[ProjectEdge] = set(graph.traverse_dependencies(edge))
		self.in_progress: MutableSet[ProjectEdge] = set()
		self.completed: MutableSet[ProjectEdge] = set()

	def fetch_ready_nodes(self) -> MutableSequence[ProjectEdge]:
		ready = []
		for node in self.pending:
			unresolved = False
			for dependency in node.dependencies:
				if dependency not in self.completed:
					unresolved = True
					break
			if not unresolved:
				ready.append(node)
		return ready

	def acquire_node(self, node: ProjectEdge) -> None:
		if node in self.pending:
			self.pending.remove(node)
			self.in_progress.add(node)

	def complete_node(self, node: ProjectEdge) -> None:
		if node in self.in_progress:
			self.in_progress.remove(node)
			self.completed.add(node)

	def has_unfinished_tasks(self) -> bool:
		return len(self.pending) > 0 or len(self.in_progress) > 0

	def is_empty(self) -> bool:
		return not self.has_unfinished_tasks()
