"""--debug: the details of every command, each line with its time, and nothing more without it."""
import contextlib
import io
import logging
from pathlib import Path
import re
import unittest

import pianasoul
from pianasoul import show_details


class DetailsTests(unittest.TestCase):
    def tearDown(self):
        log = logging.getLogger('pianasoul')
        for handler in list(log.handlers):
            log.removeHandler(handler)
        log.setLevel(logging.NOTSET)
        log.propagate = True

    def shown(self, debug):
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            show_details(debug)
            logging.getLogger('pianasoul.voice').debug('heard %s', '小茗同学')
        return out.getvalue()

    def test_without_debug_nothing_more_is_shown(self):
        self.assertEqual(self.shown(False), '')

    def test_with_debug_each_detail_has_its_time_and_where_it_comes_from(self):
        self.assertRegex(self.shown(True), re.compile(r'^    \d\d:\d\d:\d\d\.\d{3} test_details +heard 小茗同学\n$'))

    def test_every_module_logs_under_its_own_name_also_when_a_command_runs_it(self):
        # ./run.sh runs chat, voice, play and the app with python -m, where __name__ is __main__: their details
        # would not be PianaSoul's, and would not show.
        for path in Path(pianasoul.__file__).parent.glob('*.py'):
            if path.name != '__init__.py':
                self.assertNotIn('getLogger(__name__)', path.read_text(), path.name)
