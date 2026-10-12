import os
import platform
from datetime import datetime, timedelta
from io import StringIO
from typing import (Any, Callable, Dict, Iterable, List, Literal, NoReturn,
                    Optional, TypeVar, Union, overload)

from prompt_toolkit import Application, print_formatted_text
from prompt_toolkit.buffer import (Buffer, BufferAcceptHandler,
                                   BufferEventHandler)
from prompt_toolkit.document import Document
from prompt_toolkit.filters import (Condition, FilterOrBool, has_focus,
                                    to_filter)
from prompt_toolkit.formatted_text import (AnyFormattedText,
                                           StyleAndTextTuples,
                                           merge_formatted_text,
                                           to_formatted_text)
from prompt_toolkit.key_binding import (KeyBindings, KeyBindingsBase,
                                        KeyPressEvent, merge_key_bindings)
from prompt_toolkit.key_binding.bindings.focus import (focus_next,
                                                       focus_previous)
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (AnyContainer, BufferControl,
                                   ConditionalMargin, Container, Dimension,
                                   FormattedTextControl, HSplit, Layout,
                                   Margin, ScrollablePane, ScrollOffsets,
                                   SearchBufferControl, UIContent, UIControl,
                                   Window, WindowAlign, WindowRenderInfo,
                                   to_container)
from prompt_toolkit.layout.processors import (AfterInput, BeforeInput,
                                              ConditionalProcessor, Processor)
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style
from prompt_toolkit.validation import Validator

# prompt-toolkit doesn't have built-in theme styling support, which can be tracked
# on pull request https://github.com/prompt-toolkit/python-prompt-toolkit/pull/1630
# TOOLCHAIN_ANSI_DIM = "\x1b[2m"

TOOLCHAIN_STYLE = {
	# prompt-toolkit overrides
	"scrollbar.background": "bg:ansibrightblack",
	"scrollbar.button": "bg:ansiwhite",

	# logging and tty styling
	"selection": "reverse",
	"task.execute": "fg:ansibrightgreen bold",
	"print.answer": "fg:ansibrightblack",
	"print.debug": "fg:ansibrightblack",
	"print.frozen": "fg:ansibrightblue",
	"print.info": "fg:ansibrightgreen",
	"print.success": "fg:ansibrightgreen",
	"print.warn": "fg:ansibrightyellow",
	"print.attention": "fg:ansibrightyellow",
	"print.error": "fg:ansibrightred",
	"print.failure": "fg:ansibrightred",
	"print.abort-message": "fg:ansibrightred bold",

	# interactables styling
	"checkbox.inactive": "fg:ansibrightblack",
	"checkbox.active": "fg:ansibrightgreen",
	"editable.hint": "fg:ansibrightblack",
	"progress.percentage": "",
	"progress.filled": "reverse",
	"progress.unfilled": "bg:ansibrightblack",
	"progress.time-left": "",
	"paused progress.filled": "fg:ansibrightgreen",
	"interrupted progress.filled": "fg:ansibrightyellow",
	"raised progress.filled": "fg:ansibrightred",
	"margin": "",
	"debugger-overlay": "reverse",
}

def get_toolchain_style() -> Style:
	return Style.from_dict(TOOLCHAIN_STYLE)

def is_unicode_supported() -> bool:
	environ = os.environ
	terminal = environ.get("TERM", "")
	if platform.system() != "Windows":
		return terminal != "linux" # Linux console (kernel)
	return (
		terminal in {
			"xterm-256color",
			"alacritty",
			"rxvt-unicode",
			"rxvt-unicode-256color"
		}
		or environ.get("TERM_PROGRAM") in {
			"Terminus-Sublime",
			"vscode"
		}
		or bool(environ.get("WT_SESSION")) # Windows Terminal
		or bool(environ.get("TERMINUS_SUBLIME")) # Terminus (older versions)
		or environ.get("ConEmuTask") == "{cmd::Cmder}" # ConEmu/cmder
		or environ.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm"
	)

if is_unicode_supported():
	UNICODE_CHECK_MARK = "✓"
	UNICODE_POINTED_STAR = "✦"
	UNICODE_BALLOT_X = "✗"
	UNICODE_SNOWFLAKE = "❄"
	UNICODE_INTERMEDIATE_PROGRESS = ["▖", "▗", "▚","▘", "▝", "▞"]
else:
	UNICODE_CHECK_MARK = "√"
	UNICODE_POINTED_STAR = "i"
	UNICODE_BALLOT_X = "×"
	UNICODE_SNOWFLAKE = "*"
	UNICODE_INTERMEDIATE_PROGRESS = ["▀", "▄"]

interactive_application: Optional[Application] = None

def request_application(*content: Optional[AnyContainer]) -> Application:
	global interactive_application
	if interactive_application is not None:
		if interactive_application.is_running:
			if len(content) != 0:
				container = interactive_application.layout.container
				while hasattr(container, "content"):
					container = container.content # type: ignore
				assert hasattr(container, "children")
				children: List[Container] = container.children # type: ignore
				
				# append controls
				first_control = None
				for control in content:
					if control:
						wrapped = to_container(control)
						if not wrapped in children:
							children.append(wrapped)
						if not first_control:
							first_control = wrapped
							
				# focus on the newly added component
				if first_control:
					try:
						interactive_application.layout.focus(first_control)
					except ValueError:
						pass # if not focusable
				
				interactive_application.invalidate()
			return interactive_application
		interactive_application = None

	bindings = KeyBindings()
	bindings.add(Keys.Down)(focus_next)
	bindings.add(Keys.Up)(focus_previous)

	@bindings.add("c-c")
	@bindings.add("<sigint>")
	def _(event: KeyPressEvent) -> None:
		event.app.exit(exception=KeyboardInterrupt())

	layout = Layout(
		ScrollablePane(
			HSplit([
				to_container(control) for control in content if control
			]),
			scroll_offsets=ScrollOffsets(3, 3),
			display_arrows=False,
		)
	)

	interactive_application = Application(
		layout=layout,
		style=get_toolchain_style(),
		include_default_pygments_style=False,
		key_bindings=bindings,
		full_screen=False,
		mouse_support=True,
		erase_when_done=True,
	)
	return interactive_application

def clear_application(*content: Optional[AnyContainer], force_exit: bool = False) -> None:
	global interactive_application
	if interactive_application is None:
		return
	if interactive_application.is_running and len(content) != 0:
		container = interactive_application.layout.container
		while hasattr(container, "content"):
			container = container.content # type: ignore
		assert hasattr(container, "children")
		children: List[Container] = container.children # type: ignore
		for control in content:
			if control:
				wrapped = to_container(control)
				if wrapped in children:
					children.remove(wrapped)
		
		# return focus back if there are still interactive components
		if len(children) > 0 and interactive_application.layout.current_control not in children:
			try:
				interactive_application.layout.focus_last()
			except ValueError:
				pass
				
		interactive_application.invalidate()
		
		if force_exit or len(children) == 0:
			interactive_application.exit()
	if force_exit or not interactive_application.is_running or interactive_application.is_done:
		interactive_application = None

_SCT = TypeVar("_SCT", bound=Optional[AnyContainer])

class InteractiveSession(Dict[str, _SCT]):
	application: Application

	def __init__(self, **content: _SCT) -> None:
		dict.__init__(self, **content)

	def __enter__(self, *args, **kwargs) -> 'InteractiveSession[_SCT]':
		is_already_running = interactive_application is not None
		self.application = request_application(*self.values())
		if not is_already_running:
			from threading import Thread
			self.thread = Thread(target=lambda: self.application.run())
			self.thread.start()
		return self

	def __exit__(self, exc_type, exc_val, exc_tb) -> None:
		is_error = exc_type is not None
		clear_application(*self.values(), force_exit=is_error)
		if hasattr(self, "thread"):
			assert interactive_application is None
			self.thread.join()
			del self.thread

class InteractableMargin(Margin):
	def __init__(
		self,
		has_focus: FilterOrBool = False, 
		idle_selector_text: Optional[str] = "  ",
		focused_selector_text: Optional[str] = "> ",
	):
		self.has_focus = to_filter(has_focus)
		self.idle_selector_text = idle_selector_text if idle_selector_text is not None else "  "
		self.focused_selector_text = focused_selector_text if focused_selector_text is not None else "> "

	def get_width(self, get_ui_content: Callable[[], UIContent]) -> int:
		return max(len(self.idle_selector_text), len(self.focused_selector_text))

	def create_margin(self, window_render_info: WindowRenderInfo, width: int, height: int) -> StyleAndTextTuples:
		focused = self.has_focus()
		return [
			(f"class:margin", self.focused_selector_text if focused else self.idle_selector_text),
			*[(f"class:margin", self.idle_selector_text) for _ in range(height - 1)]
		]

class Interactable(FormattedTextControl):
	"""
	Pure component abstraction for all toolchain interactions in console.
	"""

	def __init__(
		self,
		text: AnyFormattedText = "",
		focusable: FilterOrBool = False,
		on_interact: Optional[Callable[['Interactable'], None]] = None,
		*,
		style: str = "",
		dont_extend_height: bool = True,
		dont_extend_width: bool = False,
		key_bindings: Optional[KeyBindingsBase] = None,
		align: Union[WindowAlign, Callable[[], WindowAlign]] = WindowAlign.LEFT,
		wrap_lines: FilterOrBool = True,
		show_cursor: bool = False,
		interact_on_enter: bool = False,
		add_interact_key_bindings: bool = False,
		idle_selector_text: Optional[str] = "  ",
		focused_selector_text: Optional[str] = "> ",
		always_indent: bool = False,
		tag: object = None,
	) -> None:
		self.interactable_text = text
		FormattedTextControl.__init__(
			self,
			text=self.render_text,
			style=style,
			focusable=focusable,
			key_bindings=key_bindings,
			show_cursor=show_cursor,
		)

		self.has_focus = has_focus(self)
		self.window = Window(
			content=self,
			height=Dimension(min=1),
			dont_extend_height=dont_extend_height,
			dont_extend_width=dont_extend_width,
			align=align,
			wrap_lines=wrap_lines,
			left_margins=[
				ConditionalMargin(
					InteractableMargin(self.has_focus, idle_selector_text, focused_selector_text),
					Condition(lambda: self.focusable() or always_indent)
				)
			],
			right_margins=[
				ConditionalMargin(
					InteractableMargin(False, idle_selector_text, focused_selector_text),
					Condition(lambda: self.focusable() or always_indent)
				)
			],
		)

		self.interact_on_enter = interact_on_enter
		self.interact_key_bindings = None
		if add_interact_key_bindings:
			self.add_interact_key_bindings()
		self.on_interact = on_interact
		self.tag = tag

	def render_text(self) -> AnyFormattedText:
		return self.interactable_text

	def add_interact_key_bindings(self) -> None:
		if not self.interact_key_bindings:
			self.interact_key_bindings = KeyBindings()
		bindings = self.interact_key_bindings

		@bindings.add(Keys.Enter, filter=Condition(lambda: self.interact_on_enter))
		@bindings.add(" ")
		def _(event: KeyPressEvent) -> None:
			self.interact(event)

	def interact(self, event: Optional[KeyPressEvent] = None) -> None:
		if self.on_interact:
			self.on_interact(self)

	def get_key_bindings(self) -> Optional[KeyBindingsBase]:
		key_bindings = super().get_key_bindings()
		if key_bindings and self.interact_key_bindings:
			return merge_key_bindings([key_bindings, self.interact_key_bindings])
		return key_bindings or self.interact_key_bindings

	def __pt_container__(self) -> Container:
		return self.window

class Selectable(Interactable):
	"""
	Extendable switch, which have checkable state and interact ability by default.
	"""

	def __init__(
		self,
		text: AnyFormattedText = "",
		focusable: FilterOrBool = True,
		on_checked: Optional[Callable[['Selectable', bool], None]] = None,
		checked: bool = False,
		*,
		style: str = "",
		dont_extend_height: bool = True,
		dont_extend_width: bool = False,
		key_bindings: Optional[KeyBindingsBase] = None,
		align: Union[WindowAlign, Callable[[], WindowAlign]] = WindowAlign.LEFT,
		wrap_lines: FilterOrBool = True,
		show_cursor: bool = False,
		add_interact_key_bindings: bool = True,
		on_interact: Optional[Callable[['Interactable'], None]] = None,
		idle_selector_text: Optional[str] = "  ",
		focused_selector_text: Optional[str] = "> ",
		always_indent: bool = False,
		unchecked_checkbox_text: Optional[str] = "[ ] ",
		checked_checkbox_text: Optional[str] = "[x] ",
		tag: object = None,
	) -> None:
		Interactable.__init__(
			self,
			text=text,
			focusable=focusable,
			on_interact=on_interact,
			style=style,
			dont_extend_height=dont_extend_height,
			dont_extend_width=dont_extend_width,
			key_bindings=key_bindings,
			align=align,
			wrap_lines=wrap_lines,
			show_cursor=show_cursor,
			add_interact_key_bindings=add_interact_key_bindings,
			idle_selector_text=idle_selector_text,
			focused_selector_text=focused_selector_text,
			always_indent=always_indent,
			tag=tag,
		)

		self.checked = checked
		self.on_checked = on_checked
		self.unchecked_checkbox_text = unchecked_checkbox_text if unchecked_checkbox_text is not None else "[ ] "
		self.checked_checkbox_text = checked_checkbox_text if checked_checkbox_text is not None else "[x] "

	def render_checkbox(self) -> AnyFormattedText:
		return [
			("class:checkbox.active", self.checked_checkbox_text) if self.checked \
				else ("class:checkbox.inactive", self.unchecked_checkbox_text)
		]

	def render_text(self) -> AnyFormattedText:
		text = Interactable.render_text(self)
		return merge_formatted_text((
			to_formatted_text(self.render_checkbox(), self.style),
			to_formatted_text(text, self.style),
		))

	def add_interact_key_bindings(self) -> None:
		Interactable.add_interact_key_bindings(self)
		bindings = self.interact_key_bindings
		assert bindings is not None

		@bindings.add("y")
		@bindings.add("Y")
		def _(event: KeyPressEvent) -> None:
			if not self.checked:
				self.interact(event)

		@bindings.add("n")
		@bindings.add("N")
		def _(event: KeyPressEvent) -> None:
			if self.checked:
				self.interact(event)

	def interact(self, event: Optional[KeyPressEvent] = None) -> None:
		self.checked = not self.checked
		if self.on_checked:
			self.on_checked(self, self.checked)
		Interactable.interact(self, event)

	def is_checked(self) -> bool:
		return self.checked

class Editable(BufferControl):
	"""
	Editable area, text can be written when input become focused, supports prompt, placeholder, etc.
	"""

	def __init__(
		self,
		prompt: AnyFormattedText = None,
		text: str = "",
		multiline: FilterOrBool = False,
		focusable: FilterOrBool = True,
		*,
		style: str = "",
		hint: Optional[str] = None,
		use_hint_as_fallback: bool = True,
		read_only: FilterOrBool = False,
        on_text_changed: Optional[BufferEventHandler] = None,
		validator: Optional[Validator] = None,
		validate_while_typing: FilterOrBool = True,
		accept_handler: Optional[BufferAcceptHandler] = None,
		dont_extend_height: bool = True,
		dont_extend_width: bool = False,
		key_bindings: Optional[KeyBindingsBase] = None,
		align: Union[WindowAlign, Callable[[], WindowAlign]] = WindowAlign.LEFT,
		wrap_lines: FilterOrBool = True,
		input_processors: Optional[List[Processor]] = None,
		include_default_input_processors: bool = True,
		lexer: Optional[Lexer] = None,
		preview_search: FilterOrBool = False,
		search_buffer_control: Optional[Union[SearchBufferControl, Callable[[], SearchBufferControl]]] = None,
		menu_position: Optional[Callable[[], Optional[int]]] = None,
		idle_selector_text: Optional[str] = "  ",
		focused_selector_text: Optional[str] = "> ",
		always_indent: bool = False,
		focus_on_click: FilterOrBool = True,
		tag: object = None,
	) -> None:
		buffer = Buffer(
			document=Document(text),
			read_only=read_only,
			# TODO: Handle arrows cursor movement for multine
			multiline=multiline,
			validator=validator,
			validate_while_typing=validate_while_typing,
			accept_handler=accept_handler,
			on_text_changed=on_text_changed,
		)
		BufferControl.__init__(
			self,
			buffer=buffer,
			input_processors=input_processors,
			include_default_input_processors=include_default_input_processors,
			lexer=lexer,
			preview_search=preview_search,
			focusable=focusable,
			search_buffer_control=search_buffer_control,
			menu_position=menu_position,
			focus_on_click=focus_on_click,
			key_bindings=key_bindings,
		)

		self.has_focus = has_focus(self)
		self.window = Window(
			content=self,
			height=Dimension(min=1),
			dont_extend_height=dont_extend_height,
			dont_extend_width=dont_extend_width,
			align=align,
			wrap_lines=wrap_lines,
			left_margins=[
				ConditionalMargin(
					InteractableMargin(self.has_focus, idle_selector_text, focused_selector_text),
					Condition(lambda: self.focusable() or always_indent)
				)
			],
			right_margins=[
				ConditionalMargin(
					InteractableMargin(False, idle_selector_text, focused_selector_text),
					Condition(lambda: self.focusable() or always_indent)
				)
			],
		)

		self.style = style
		self.prompt = prompt
		self.hint = hint
		self.use_hint_as_fallback = use_hint_as_fallback

		if not self.input_processors:
			self.input_processors = []
		self.input_processors.extend((
			ConditionalProcessor(
				BeforeInput(" "),
				Condition(self.has_prompt)
			),
			ConditionalProcessor(
				BeforeInput(lambda: self.prompt),
				Condition(self.has_prompt)
			),
			ConditionalProcessor(
				AfterInput(lambda: to_formatted_text(self.hint if self.hint is not None else "...", style="class:editable.hint")),
				Condition(self.has_hint)
			),
		))

		self.interact_key_bindings = None
		self.add_interact_key_bindings()
		self.tag = tag

	def has_prompt(self) -> bool:
		return self.prompt is not None and len(to_formatted_text(self.prompt, self.style)) > 0

	def has_hint(self) -> bool:
		return len(self.buffer.text) == 0

	def is_interactable(self) -> bool:
		return len(self.buffer.text) == 0 and self.use_hint_as_fallback and self.hint is not None and not self.buffer.read_only()

	def add_interact_key_bindings(self) -> None:
		if not self.interact_key_bindings:
			self.interact_key_bindings = KeyBindings()
		bindings = self.interact_key_bindings

		@bindings.add(" ", filter=Condition(self.is_interactable))
		def _(event: KeyPressEvent) -> None:
			self.interact(event)

	def interact(self, event: Optional[KeyPressEvent] = None) -> None:
		if self.is_interactable():
			assert self.hint is not None
			self.buffer.document = Document(self.hint)

	def get_value(self, fallback_allowed: bool = True) -> Optional[str]:
		if len(self.buffer.text) > 0:
			return self.buffer.text
		if fallback_allowed and self.use_hint_as_fallback:
			return self.hint
		return None

	def get_key_bindings(self) -> Optional[KeyBindingsBase]:
		key_bindings = super().get_key_bindings()
		if key_bindings and self.interact_key_bindings:
			return merge_key_bindings([key_bindings, self.interact_key_bindings])
		return key_bindings or self.interact_key_bindings

	def __pt_container__(self) -> Container:
		return self.window

def format_timedelta(timedelta: timedelta) -> str:
    result = f"{timedelta}".split(".")[0]
    if result.startswith("0:"):
        result = result[2:]
    return result

class Progress(UIControl):
	"""
	Percentage bar with left time and interaction ability.
	"""

	def __init__(
		self,
		text: Optional[str] = None,
		focusable: FilterOrBool = False,
		percentage: float = 0.0,
		on_interact: Optional[Callable[['Progress'], None]] = None,
		*,
		intermediate: bool = False,
		style: str = "",
		display_states: bool = True,
		dont_extend_height: bool = True,
		dont_extend_width: bool = False,
		key_bindings: Optional[KeyBindingsBase] = None,
		align: Union[WindowAlign, Callable[[], WindowAlign]] = WindowAlign.LEFT,
		wrap_lines: FilterOrBool = True,
		interact_on_enter: bool = False,
		add_interact_key_bindings: bool = False,
		idle_selector_text: Optional[str] = "  ",
		focused_selector_text: Optional[str] = "> ",
		always_indent: bool = False,
		tag: object = None,
	):
		self.done = False
		self.start_time = datetime.now()
		self.stopped = False
		self.stop_time = None
		self.percentage = percentage
		self.text = text
		self.display_states = display_states
		self.intermediate = intermediate

		self.focusable = to_filter(focusable)
		self.has_focus = has_focus(self)
		self.window = Window(
			content=self,
			height=Dimension(min=1),
			dont_extend_height=dont_extend_height,
			dont_extend_width=dont_extend_width,
			align=align,
			wrap_lines=wrap_lines,
			left_margins=[
				ConditionalMargin(
					InteractableMargin(self.has_focus, idle_selector_text, focused_selector_text),
					Condition(lambda: self.focusable() or always_indent)
				)
			],
			right_margins=[
				ConditionalMargin(
					InteractableMargin(False, idle_selector_text, focused_selector_text),
					Condition(lambda: self.focusable() or always_indent)
				)
			],
		)

		self.style = style
		self.key_bindings = key_bindings
		self.interact_on_enter = interact_on_enter
		self.interact_key_bindings = None
		if add_interact_key_bindings:
			self.add_interact_key_bindings()
		self.on_interact = on_interact
		self.tag = tag

	def is_focusable(self) -> bool:
		return self.focusable()

	def add_interact_key_bindings(self) -> None:
		if not self.interact_key_bindings:
			self.interact_key_bindings = KeyBindings()
		bindings = self.interact_key_bindings

		@bindings.add(Keys.Enter, filter=Condition(lambda: self.interact_on_enter))
		@bindings.add(" ")
		def _(event: KeyPressEvent) -> None:
			self.interact(event)

	def interact(self, event: Optional[KeyPressEvent] = None) -> None:
		if self.on_interact:
			self.on_interact(self)

	def get_key_bindings(self) -> Optional[KeyBindingsBase]:
		if self.key_bindings and self.interact_key_bindings:
			return merge_key_bindings([self.key_bindings, self.interact_key_bindings])
		return self.key_bindings or self.interact_key_bindings

	def render(self, offset: int, width: int) -> AnyFormattedText:
		if self.intermediate:
			return self.render_intermediate(offset, width)
		return self.render_progress(offset, width)

	def render_progress(self, offset: int, width: int) -> AnyFormattedText:
		available_width = width
		if self.display_states:
			time_left = self.time_left()
			percentage_text = f"{self.percentage * 100:.1f}% "
			time_left_text = f" {format_timedelta(time_left) if time_left else 'N/A'}"
			available_width = available_width - len(percentage_text) - len(time_left_text)

		filled_progress_width = int(self.percentage * available_width)
		bar_text = self.text.center(available_width) if self.text else " " * available_width

		if self.display_states:
			return [
				("class:progress.percentage", percentage_text),
				("class:progress.filled", bar_text[:filled_progress_width]),
				("class:progress.unfilled", bar_text[filled_progress_width:]),
				("class:progress.time-left", time_left_text),
			]
		else:
			return [
				("class:progress.filled", bar_text[:filled_progress_width]),
				("class:progress.unfilled", bar_text[filled_progress_width:]),
			]

	def render_intermediate(self, offset: int, width: int) -> AnyFormattedText:
		if not hasattr(self, "intermediate_frame"):
			self.intermediate_frame = 0
			self.intermediate_frame_monotic = 0
		from time import monotonic
		current_monotic = monotonic()
		if current_monotic - self.intermediate_frame_monotic > 0.2:
			self.intermediate_frame_monotic = current_monotic
			self.intermediate_frame += 1
			if self.intermediate_frame >= len(UNICODE_INTERMEDIATE_PROGRESS):
				self.intermediate_frame = 0
		return [
			("", f"{UNICODE_INTERMEDIATE_PROGRESS[self.intermediate_frame]} "),
			("", self.text if self.text is not None else "Please wait...")
		]

	def create_content(self, width: int, height: int) -> UIContent:
		return UIContent(
			get_line=lambda offset: to_formatted_text(
				self.render(offset, width),
				self.style
			),
			line_count=1,
			show_cursor=False
		)

	def update(self, percentage: float, text: Optional[str] = None) -> None:
		self.percentage = max(0, min(1.0, percentage))
		if text is not None:
			self.text = text

	def time_elapsed(self) -> timedelta:
		if self.stop_time is None:
			return datetime.now() - self.start_time
		else:
			return self.stop_time - self.start_time

	def time_left(self) -> Optional[timedelta]:
		if not self.percentage:
			return None
		elif self.done or self.stopped:
			return timedelta(0)
		else:
			return self.time_elapsed() * (1.0 - self.percentage) / self.percentage

	def __pt_container__(self) -> Container:
		return self.window

class Debugger(Interactable):
	"""
	Debugging stuff considered from content with max available width. 
	"""

	def __init__(
		self,
		*,
		align: Union[WindowAlign, Callable[[], WindowAlign]] = WindowAlign.LEFT,
		tag: object = None,
	) -> None:
		Interactable.__init__(
			self,
			text="N/A",
			focusable=False,
			dont_extend_height=True,
			dont_extend_width=True,
			align=align,
			wrap_lines=False,
			tag=tag,
		)

	def preferred_width(self, max_available_width: int) -> int:
		self._max_available_width = max_available_width
		return super().preferred_width(max_available_width)

	def render_text(self) -> AnyFormattedText:
		from prompt_toolkit.application import get_app
		app = get_app()
		screen = app.renderer.last_rendered_screen
		max_available_width = 0
		if hasattr(self, "_max_available_width"):
			max_available_width = self._max_available_width
		if max_available_width <= 0 and screen is not None:
			max_available_width = screen.width
		if max_available_width <= 0:
			return super().render_text()
		buffer = []
		if screen is not None:
			buffer.append(f"{screen.width}x{screen.height}{'f' if screen.show_cursor else 'h'}")
		buffer.append(f"{app.color_depth.value.split('_', 3)[1]}d/")
		current_buffer = app.layout.current_buffer
		if current_buffer is not None:
			buffer.append(f"{len(current_buffer.text)}b")
		current_control = app.layout.current_control
		if current_control is not None:
			buffer.append(current_control.__class__.__name__)
		current_window = app.layout.current_window
		if current_window is not None:
			render_info = current_window.render_info
			if render_info is not None:
				buffer.append(f"{render_info.window_width}x{render_info.window_height}{'n' if render_info.wrap_lines else 's'}")
		if current_buffer is None and current_control is None and current_window is None:
			buffer.append("inactive")
		buffer.append(f"/{sum(1 for _ in app.layout.find_all_controls())}c")
		buffer.append(f"{len(app.layout.visible_windows)}vw")
		buffer.append(f"{sum(1 for _ in app.layout.find_all_windows())}w")
		text = " " + "".join(buffer) + " "
		if len(text) > max_available_width:
			text = text[:max_available_width - 2] + "+ "
		return [
			("class:debugger-overlay", text.center(max_available_width, "▄").replace("▄▄", "▄▀")),
		]


@overload
def select_prompt(prompt: Optional[str] = None, *variants: str, selected_variant: Optional[Union[str, int]] = None, returns_what: Literal[False] = False, fallback: Optional[Union[str, int]] = None, prints_abort: bool = True) -> Optional[int]:
	...
@overload
def select_prompt(prompt: Optional[str] = None, *variants: str, selected_variant: Optional[Union[str, int]] = None, returns_what: Literal[True] = True, fallback: Optional[Union[str, int]] = None, prints_abort: bool = True) -> Optional[str]:
	...

def select_prompt(prompt: Optional[str] = None, *variants: str, selected_variant: Optional[Union[str, int]] = None, returns_what: bool = False, fallback: Optional[Union[str, int]] = None, prints_abort: bool = True) -> Optional[Union[int, str]]:
	from .prompt import Select
	select = Select(prompt=prompt, variants=variants, selected_variant=selected_variant, default_variant=fallback, returns_what=returns_what)
	return select.request_safe(prints_abort=prints_abort)

def input_prompt(prompt: Optional[str] = None, default_text: Optional[str] = None, explanation: Optional[str] = None, on_text_changed: Optional[Callable[[Editable, Interactable], None]] = None, fallback: Optional[str] = None, prints_abort: bool = True) -> Optional[str]:
	from .prompt import Input

	def on_input(input: Input, text: AnyFormattedText) -> None:
		if on_text_changed:
			on_text_changed(input.input_control, input.explanation_control)

	input = Input(prompt=prompt, hint=fallback, explanation=explanation, default_text=default_text or "", on_input=on_input)
	return input.request_safe(prints_abort=prints_abort)

def confirm_prompt(prompt: Optional[str] = None, fallback: bool = True, explanation: Optional[str] = None, prints_abort: bool = True) -> bool:
	from .prompt import Confirm
	confirm = Confirm(prompt=prompt, explanation=explanation, default_value=fallback)
	return confirm.request_safe(prints_abort=prints_abort)

def stringify(*values: object, sep: Optional[str] = " ", end: Optional[str] = "") -> str:
	buffer = StringIO()
	print(*values, sep=sep, end=end, file=buffer)
	return buffer.getvalue()

def link(text: str, url: Optional[str] = None) -> str:
	return f"\x1b]8;;{url or text}\a{text}\x1b]8;;\a"

def image(base64: str, options: Optional[Dict[str, object]] = None) -> str:
	returnValue = "\x1b]1337;File=inline=1"
	if options:
		if "width" in options:
			returnValue += ";width=" + str(options["width"])
		if "height" in options:
			returnValue += ";height=" + str(options["height"])
		if "preserveAspectRatio" in options and options["preserveAspectRatio"] == False:
			returnValue += ";preserveAspectRatio=0"
	return f"{returnValue}:{base64}\a"

def pretty_print(*values: object, style: str = "", sep: Optional[str] = " ", end: Optional[str] = "\n", file: Optional[Any] = None, flush: bool = False, include_default_pygments_style: bool = False) -> None:
	baked_style = get_toolchain_style()
	print_something = False
	for value in values:
		if print_something and sep is not None and len(sep) > 0:
			print_formatted_text(to_formatted_text(sep, style=style), sep="", end="", file=file, flush=flush, style=baked_style, include_default_pygments_style=include_default_pygments_style)
		text = to_formatted_text(value, style=style, auto_convert=True) # type: ignore
		print_formatted_text(text, end="", file=file, flush=flush, style=baked_style, include_default_pygments_style=include_default_pygments_style)
		print_something = True
	if end and "\r" in end:
		print(end, end="", file=file, flush=True)
	elif end != "":
		print_formatted_text(end or "\n", end="", file=file, flush=flush, style=baked_style, include_default_pygments_style=include_default_pygments_style)

def pretty_ansi_layers(*layers: Iterable[Union[int, str]], end: Optional[str] = "", file: Optional[Any] = None, flush: bool = False, include_default_pygments_style: bool = False) -> None:
	from prompt_toolkit import ANSI
	for layer in layers:
		for symbol in layer:
			prefix = "" if symbol == 0 or symbol == 7 else "7m" if not isinstance(symbol, int) else "7m\x1b[" if symbol == 2 else "48;5;"
			postfix = "m  " if isinstance(symbol, int) else ""
			pretty_print(ANSI(f"\x1b[{prefix}{symbol}{postfix}"), end="", file=file, flush=flush, include_default_pygments_style=include_default_pygments_style) # "\x1b[0m"
		pretty_print(file=file, flush=flush, include_default_pygments_style=include_default_pygments_style)
	if end:
		pretty_print(end, end="", file=file, flush=flush, include_default_pygments_style=include_default_pygments_style)

if __name__ == "__main__":
	preparing = Progress(intermediate=True)
	progress = Progress(intermediate=True)
	progress2 = Progress("I'm abobus", percentage=1.0)
	application = request_application(preparing, progress, progress2)
	import asyncio
	async def in_coroutine():
		async def update_progress():
			while progress.percentage < 1.0:
				progress.update(progress.percentage + 0.015)
				progress2.update(progress.percentage - 0.015)
				await asyncio.sleep(0.1)
			application.exit()
		application.create_background_task(update_progress())
		await application.run_async()
	asyncio.run(in_coroutine())
	clear_application(preparing, progress, progress2)

	import time
	with InteractiveSession(preparing=Progress(intermediate=True), progress=Progress("I'm aboba"), progress2=Progress("I'm abobus", percentage=1.0)) as session:
		while session["progress"].percentage < 1.0:
			session["progress"].update(session["progress"].percentage + 0.015)
			session["progress2"].update(session["progress2"].percentage - 0.015)
			time.sleep(0.1)
