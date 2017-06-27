# Copyright 2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""maasserver fixtures."""

__all__ = [
    "IntroCompletedFixture",
    "PackageRepositoryFixture",
]

import inspect
import logging

import fixtures
from maasserver.models.config import Config
from maasserver.testing.factory import factory


class PackageRepositoryFixture(fixtures.Fixture):
    """Insert the base PackageRepository entries."""

    def _setUp(self):
        factory.make_default_PackageRepositories()


class IntroCompletedFixture(fixtures.Fixture):
    """Mark intro as completed as default."""

    def _setUp(self):
        Config.objects.set_config("completed_intro", True)


class StacktraceFilter(logging.Filter):
    """Injects stack trace information when added as a filter to a logger."""

    def filter(self, record):
        source_trace = ''
        stack = inspect.stack()
        for s in reversed(stack):
            line = s[2]
            file = '/'.join(s[1].split('/')[-3:])
            calling_method = s[3]
            source_trace += '%s in %s at %s\n' % (line, file, calling_method)
        record.sourcetrace = source_trace
        del stack
        return True


class LogSQL(fixtures.Fixture):
    """Logs SQL to standard out.

    This should only be used for debugging a single test. It should never
    land in trunk on an actual test.
    """

    def __init__(self, include_stacktrace=False):
        super(LogSQL, self).__init__()
        self.include_stacktrace = include_stacktrace

    def _setUp(self):
        log = logging.getLogger('django.db.backends')
        self._origLevel = log.level
        self._setHandler = logging.StreamHandler()
        if self.include_stacktrace:
            self._addedFilter = StacktraceFilter()
            log.addFilter(self._addedFilter)
            self._setHandler.setFormatter(
                logging.Formatter(
                    '-' * 80 + '\n%(sql)s\n\nStacktrace of SQL query '
                    'producer:\n%(sourcetrace)s' + '-' * 80 + '\n'))
        log.setLevel(logging.DEBUG)
        log.addHandler(self._setHandler)

    def _tearDown(self):
        log = logging.getLogger('django.db.backends')
        log.setLevel(self._origLevel)
        if self.include_stacktrace:
            log.removeFilter(self._addedFilter)
        self.removeHandler(self._setHandler)