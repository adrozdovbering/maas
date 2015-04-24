# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test the maas package."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from importlib import import_module
import new
import os.path
import sys
from textwrap import dedent
from unittest import skipIf

from django.conf import settings
from django.db import connections
from fixtures import PythonPathEntry
from maas import (
    find_settings,
    import_local_settings,
    import_settings,
)
from maastesting.djangotestcase import DjangoTestCase
from maastesting.factory import factory
from psycopg2.extensions import ISOLATION_LEVEL_REPEATABLE_READ
from testtools.matchers import (
    ContainsDict,
    Equals,
    Is,
)


class TestSettingsHelpers(DjangoTestCase):
    """Test Django settings helper functions."""

    def test_find_settings(self):
        # find_settings() returns a dict of settings from a Django-like
        # settings file. It excludes settings beginning with underscores.
        module = new.module(b"example")
        module.SETTING = factory.make_string()
        module._NOT_A_SETTING = factory.make_string()
        expected = {"SETTING": module.SETTING}
        observed = find_settings(module)
        self.assertEqual(expected, observed)

    def test_import_settings(self):
        # import_settings() copies settings from another module into the
        # caller's global scope.
        source = new.module(b"source")
        source.SETTING = factory.make_string()
        target = new.module(b"target")
        target._source = source
        target._import_settings = import_settings
        eval("_import_settings(_source)", vars(target))
        expected = {"SETTING": source.SETTING}
        observed = find_settings(target)
        self.assertEqual(expected, observed)

    local_settings_module = b"maas_local_settings"

    def _test_import_local_settings(self):
        # import_local_settings() copies settings from the local settings
        # module into the caller's global scope.
        target = new.module(b"target")
        target._import_local_settings = import_local_settings
        eval("_import_local_settings()", vars(target))
        source = import_module(self.local_settings_module)
        expected = find_settings(source)
        observed = find_settings(target)
        self.assertEqual(expected, observed)

    @skipIf(
        local_settings_module in sys.modules,
        "%s already imported." % local_settings_module)
    def test_import_local_settings_1(self):
        # The local settings module has not yet been imported, so fake one.
        config = dedent("""
            SETTING = %r
            _NOT_A_SETTING = %r
            """ % (factory.make_string(), factory.make_string()))
        module = self.make_file(
            name=b"%s.py" % self.local_settings_module, contents=config)
        module_dir, module_file = os.path.split(module)
        self.addCleanup(sys.modules.pop, self.local_settings_module, None)
        self.useFixture(PythonPathEntry(module_dir))
        self._test_import_local_settings()

    @skipIf(
        local_settings_module not in sys.modules,
        "%s not yet imported." % local_settings_module)
    def test_import_local_settings_2(self):
        # The local settings module has been imported, so test with that.
        self._test_import_local_settings()


class TestDatabaseConfiguration(DjangoTestCase):

    def test_transactionmiddleware_is_not_used(self):
        # The 'TransactionMiddleware' is not enabled (it has been
        # deprecated by the Django project).
        self.assertNotIn(
            'django.middleware.transaction.TransactionMiddleware',
            settings.MIDDLEWARE_CLASSES)

    def test_atomic_requests_are_enabled(self):
        # ATOMIC_REQUESTS *must* be set for the default connection.
        self.assertThat(
            connections.databases, ContainsDict({
                "default": ContainsDict({
                    "ATOMIC_REQUESTS": Is(False),
                }),
            }),
        )

    def test_isolation_level_is_serializable(self):
        # Transactions *must* be SERIALIZABLE for the default connection.
        self.assertThat(
            connections.databases, ContainsDict({
                "default": ContainsDict({
                    "OPTIONS": ContainsDict({
                        "isolation_level": Equals(
                            ISOLATION_LEVEL_REPEATABLE_READ),
                    }),
                }),
            }),
        )