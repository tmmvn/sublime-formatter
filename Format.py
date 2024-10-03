"""
Copyright (c) 2012 Timon Wong

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import sublime
import sublime_plugin
import json
import re
import os
import sys
from . import lib
from .FormatterLib import get_syntax_mode_mapping
from .FormatterLib.MergeUtils import merge_code
from .FormatterLib import FormatterOptions


__file__ = os.path.normpath(os.path.abspath(__file__))
__path__ = os.path.dirname(__file__)
PLUGIN_NAME = 'Formatter'
SYNTAX_RE = re.compile(r'(?<=source\.)[\w+#]+')


with open(os.path.join(__path__, 'options_default.json')) as fp:
    OPTIONS_DEFAULT = json.load(fp)


def log(level, fmt, args):
    s = PLUGIN_NAME + ': [' + level + '] ' + (fmt.format(*args))
    print(s)


def log_debug(fmt, *args):
    log('DEBUG', fmt, args)


def load_settings():
    return sublime.load_settings(PLUGIN_NAME + '.sublime-settings')


_VARPROG_RE = re.compile(r'\$(\w+|\{[^}]*\})')


def custom_expandvars(path, custom_envs):
    if '$' not in path:
        return path
    envs = custom_envs.copy()
    envs.update(os.environ)
    i = 0
    while True:
        m = _VARPROG_RE.search(path, i)
        if not m:
            break
        i, j = m.span(0)
        name = m.group(1)
        if name.startswith('{') and name.endswith('}'):
            name = name[1:-1]
        if name in envs:
            tail = path[j:]
            path = path[:i] + envs[name]
            i = len(path)
            path += tail
        else:
            i = j
    return path


def get_settings_for_view(view, key, default=None):
    try:
        settings = view.settings()
        sub_key = 'Formatter'
        if settings.has(sub_key):
            proj_settings = settings.get(sub_key)
            if key in proj_settings:
                return proj_settings[key]
    except:
        pass
    settings = load_settings()
    return settings.get(key, default)


def get_settings_for_active_view(key, default=None):
    return get_settings_for_view(
        sublime.active_window().active_view(), key, default)


def get_syntax_for_view(view):
    caret = view.sel()[0].a
    syntax = SYNTAX_RE.search(view.scope_name(caret))
    if syntax is None:
        return ''
    return syntax.group(0).lower()


def is_supported_syntax(view, syntax):
    mapping = get_settings_for_view(
        view, 'user_defined_syntax_mode_mapping', {})
    return syntax in get_syntax_mode_mapping(mapping)


def is_enabled_in_view(view):
    syntax = get_syntax_for_view(view)
    return is_supported_syntax(view, syntax)


class FormatfooCommand(sublime_plugin.TextCommand):
    _STRIP_LEADING_SPACES_RE = re.compile(r'[ \t]*\n([^\r\n])')
    _SKIP_COMMENT_RE = re.compile(r'\s*\#')

    def _get_settings(self, key, default=None):
        return get_settings_for_view(self.view, key, default=default)

    def _get_syntax_settings(self, syntax, formatting_mode):
        key = 'options_%s' % formatting_mode
        settings = get_settings_for_view(self.view, key, default={})
        if syntax and syntax != formatting_mode:
            key = 'options_%s' % syntax
            settings_override = get_settings_for_view(
                self.view, key, default={})
            settings.update(settings_override)
        return settings

    def _get_default_options(self):
        options_default = OPTIONS_DEFAULT.copy()
        options_default_override = self._get_settings(
            'options_default', default={})
        options_default.update(options_default_override)
        return options_default

    def _build_custom_vars(self):
        view = self.view
        custom_vars = {
            'packages': sublime.packages_path(),
        }
        full_path = view.file_name()
        if full_path:
            file_name = os.path.basename(full_path)
            file_base_name, file_extension = os.path.splitext(file_name)
            custom_vars.update({
                'file_path': os.path.dirname(full_path),
                'file': full_path,
                'file_name': file_name,
                'file_extension': file_extension,
                'file_base_name': file_base_name,
            })
        window = view.window()
        project_file_name = window.project_file_name()
        if project_file_name:
            project_name = os.path.basename(project_file_name)
            project_base_name, project_extension = os.path.splitext(
                project_name)
            custom_vars.update({
                'project': project_file_name,
                'project_path': os.path.dirname(project_file_name),
                'project_name': project_name,
                'project_extension': project_extension,
                'project_base_name': project_base_name,
            })
        return custom_vars

    def _read_stylerc(self, path):
        # Expand environment variables first
        fullpath = custom_expandvars(path, self._build_custom_vars())
        if not os.path.isfile(fullpath):
            return ''
        try:
            lines = []
            with open(fullpath, 'r') as f:
                for line in f:
                    if not self._SKIP_COMMENT_RE.match(line):
                        lines.append(line.strip())
            return ' '.join(lines)
        except Exception:
            return ''

    @staticmethod
    def _join_options(options_list):
        return FormatterOptions.strip_invalid_options_string(
            ' '.join(o for o in options_list if o))

    def _get_options(self, syntax, formatting_mode):
        syntax_settings = self._get_syntax_settings(syntax, formatting_mode)
        # --mode=xxx placed first
        options_list = [FormatterOptions.build_style_mode_option(formatting_mode)]
        if 'additional_options_file' in syntax_settings:
            stylerc_options = self._read_stylerc(
                syntax_settings['additional_options_file'])
        else:
            stylerc_options = ''
        if 'additional_options' in syntax_settings:
            additional_options = ' '.join(
                syntax_settings['additional_options'])
        else:
            additional_options = ''
        options_list.append(additional_options)
        options_list.append(stylerc_options)
        # Check if user will use only additional options, skip processing other
        # options when 'use_only_additional_options' is true
        if syntax_settings.get('use_only_additional_options', False):
            return self._join_options(options_list)
        # Get default options
        default_settings = self._get_default_options()
        # Merge syntax_settings with default_settings
        default_settings.update(syntax_settings)
        options = FormatterOptions.build_style_options(
            default_settings,
            self._build_indent_options(),
            convert_tabs=self._should_convert_tabs()
        )
        options = ' '.join(options)
        options_list.insert(1, options)
        return self._join_options(options_list)

    def _build_indent_options(self):
        view_settings = self.view.settings()
        return {
            'indent': 'spaces'
                      if view_settings.get('translate_tabs_to_spaces')
                      else 'tab',
            'spaces': view_settings.get('tab_size'),
        }

    def _should_convert_tabs(self):
        view_settings = self.view.settings()
        return view_settings.get('translate_tabs_to_spaces')

    def _get_formatting_mode(self, syntax):
        mapping = get_settings_for_view(
            self.view, 'user_defined_syntax_mode_mapping', {})
        return get_syntax_mode_mapping(mapping).get(syntax, '')

    def run(self, edit, selection_only=False):
        error_panel = ErrorMessagePanel("style_error_message")
        error_panel.close()
        try:
            # Loading options
            syntax = get_syntax_for_view(self.view)
            formatting_mode = self._get_formatting_mode(syntax)
            options = self._get_options(syntax, formatting_mode)
        except FormatterOptions.ImproperlyConfigured as e:
            extra_message = e.extra_message
            error_panel = ErrorMessagePanel("style_error_message")
            error_panel.write(
                "%s: An error occurred while processing options: %s\n\n" % (
                    PLUGIN_NAME, e))
            if extra_message:
                error_panel.write("* %s\n" % extra_message)
            error_panel.show()
            return
        # Options ok, format now
        try:
            if selection_only:
                self.run_selection_only(edit, options)
            else:
                self.run_whole_file(edit, options)
        except lib.error as e:
            error_panel.write(
                "%s: An error occurred while formatting using astyle: %s\n\n"
                % (PLUGIN_NAME, e))
            error_panel.show()
            return
        if self._get_settings('debug', False):
            log_debug('Style version: {0}', lib.version())
            log_debug('Style options: ' + options)
        sublime.status_message('Style (v%s) Formatted' % lib.version())

    def get_line_indentation_pos(self, view, point):
            line_region = view.line(point)
            pos = line_region.a
            end = line_region.b
            while pos < end:
                ch = view.substr(pos)
                if ch != ' ' and ch != '\t':
                    break
                pos += 1
            return pos

    def get_indentation_count(self, view, start):
        indent_count = 0
        i = start - 1
        while i > 0:
            ch = view.substr(i)
            scope = view.scope_name(i)
            # Skip preprocessors, strings, characaters and comments
            if ('string.quoted' in scope or
                    'comment' in scope or 'preprocessor' in scope):
                extent = view.extract_scope(i)
                i = extent.a - 1
                continue
            else:
                i -= 1
            if ch == '}':
                indent_count -= 1
            elif ch == '{':
                indent_count += 1
        return indent_count

    def run_selection_only(self, edit, options):
        view = self.view
        regions = []
        for sel in view.sel():
            start = self.get_line_indentation_pos(view, min(sel.a, sel.b))
            region = sublime.Region(
                view.line(start).a,  # line start of first line
                view.line(max(sel.a, sel.b)).b)  # line end of last line
            indent_count = self.get_indentation_count(view, start)
            # Add braces for indentation hack
            text = '{' * indent_count
            if indent_count > 0:
                text += '\n'
            text += view.substr(region)
            # Performing astyle formatter
            formatted_code = lib.format(text, options)
            if indent_count > 0:
                for _ in range(indent_count):
                    index = formatted_code.find('{') + 1
                    formatted_code = formatted_code[index:]
                formatted_code = self._STRIP_LEADING_SPACES_RE.sub(
                    r'\1', formatted_code, 1)
            else:
                # HACK: While no identation, a '{' will generate a blank line,
                # so strip it.
                search = '\n{'
                if search not in text:
                    formatted_code = formatted_code.replace(search, '{', 1)
            # Applying formatted text
            view.replace(edit, region, formatted_code)
            # Region for replaced text
            if sel.a <= sel.b:
                regions.append(
                    sublime.Region(region.a, region.a + len(formatted_code)))
            else:
                regions.append(
                    sublime.Region(region.a + len(formatted_code), region.a))
        view.sel().clear()
        # Add regions of formatted text
        [view.sel().add(region) for region in regions]

    def run_whole_file(self, edit, options):
        view = self.view
        region = sublime.Region(0, view.size())
        code = view.substr(region)
        # Performing astyle formatter
        formatted_code = lib.format(code, options)
        # Replace to view
        _, err = merge_code(view, edit, code, formatted_code)
        if err:
            error_panel = ErrorMessagePanel("style_error_message")
            error_panel.write('%s: Merge failure: "%s"\n' % (PLUGIN_NAME, err))

    #def is_enabled(self):
    #   return is_enabled_in_view(self.view)


class PluginEventListener(sublime_plugin.EventListener):
    def on_pre_save(self, view):
        if is_enabled_in_view(view) and get_settings_for_active_view(
                'autoformat_on_save', default=False):
            view.run_command('styleformat')

    def on_query_context(self, view, key, operator, operand, match_all):
        if key == 'styleformat_is_enabled':
            return is_enabled_in_view(view)
        return None


class StylePanelInsertCommand(sublime_plugin.TextCommand):
    def run(self, edit, text):
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), text)
        self.view.set_read_only(True)
        self.view.show(self.view.size())


class ErrorMessagePanel(object):
    def __init__(self, name, under_unittest=False, word_wrap=False,
                 line_numbers=False, gutter=False, scroll_past_end=False,
                 syntax='Packages/Text/Plain text.tmLanguage'):
        self.name = name
        self.window = None
        self.output_view = None
        self.window = sublime.active_window()
        self.output_view = self.window.get_output_panel(name)
        settings = self.output_view.settings()
        settings.set("word_wrap", word_wrap)
        settings.set("line_numbers", line_numbers)
        settings.set("gutter", gutter)
        settings.set("scroll_past_end", scroll_past_end)
        settings.set("syntax", syntax)

    def write(self, s):
        if self.output_view:
            self.output_view.run_command('style_panel_insert', {'text': s})

    def show(self):
        if self.output_view:
            self.window.run_command(
                "show_panel", {"panel": "output." + self.name})

    def close(self):
        if self.output_view:
            self.window.run_command(
                "hide_panel", {"panel": "output." + self.name})
