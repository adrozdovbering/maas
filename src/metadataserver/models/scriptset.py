# Copyright 2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__all__ = [
    "ScriptSet",
]
from datetime import timedelta

from django.core.exceptions import (
    ObjectDoesNotExist,
    ValidationError,
)
from django.db.models import (
    CASCADE,
    CharField,
    DateTimeField,
    ForeignKey,
    IntegerField,
    Manager,
    Model,
    Q,
)
from maasserver.enum import (
    POWER_STATE,
    POWER_STATE_CHOICES,
)
from maasserver.exceptions import NoScriptsFound
from maasserver.models import Config
from maasserver.models.cleansave import CleanSave
from maasserver.preseed import CURTIN_INSTALL_LOG
from metadataserver import DefaultMeta
from metadataserver.enum import (
    RESULT_TYPE,
    RESULT_TYPE_CHOICES,
    SCRIPT_STATUS,
    SCRIPT_STATUS_CHOICES,
    SCRIPT_TYPE,
)
from metadataserver.models.script import Script
from provisioningserver.refresh.node_info_scripts import NODE_INFO_SCRIPTS


class ScriptSetManager(Manager):

    def _clean_old(self, node, result_type, new_script_set):
        # Gather the list of existing script results of the given type for this
        # node.
        script_sets = self.filter(node=node, result_type=result_type)
        # Exclude the newly created script_set so we don't try to remove it.
        # This can happen when multiple script_sets have last_ping = None.
        script_sets = script_sets.exclude(id=new_script_set.id)
        # Sort by last_ping in reverse order so we only remove older entrees.
        script_sets = script_sets.order_by('last_ping').reverse()
        config_var = {
            RESULT_TYPE.COMMISSIONING: 'max_node_commissioning_results',
            RESULT_TYPE.TESTING: 'max_node_testing_results',
            RESULT_TYPE.INSTALLATION: 'max_node_installation_results',
        }
        script_set_limit = Config.objects.get_config(config_var[result_type])
        # Remove one from the script_set_limit to account for the newly created
        # script_set.
        script_set_limit -= 1
        if script_sets.count() > script_set_limit:
            for script_set in script_sets[script_set_limit:]:
                script_set.delete(force=True)

    def create_commissioning_script_set(self, node, scripts=[]):
        """Create a new commissioning ScriptSet with ScriptResults

        ScriptResults will be created for all builtin commissioning scripts.
        Optionally a list of user scripts and tags can be given to create
        ScriptResults for. If None all user scripts will be assumed.
        """
        # Avoid circular dependencies.
        from metadataserver.models import ScriptResult

        script_set = self.create(
            node=node, result_type=RESULT_TYPE.COMMISSIONING,
            power_state_before_transition=node.power_state)
        self._clean_old(node, RESULT_TYPE.COMMISSIONING, script_set)

        for script_name, data in NODE_INFO_SCRIPTS.items():
            if node.is_controller and not data['run_on_controller']:
                continue
            ScriptResult.objects.create(
                script_set=script_set, status=SCRIPT_STATUS.PENDING,
                script_name=script_name)

        # MAAS doesn't run custom commissioning scripts during controller
        # refresh.
        if node.is_controller:
            return script_set

        if scripts == []:
            qs = Script.objects.filter(
                script_type=SCRIPT_TYPE.COMMISSIONING)
        else:
            ids = [
                int(id)
                for id in scripts
                if isinstance(id, int) or id.isdigit()
            ]
            qs = Script.objects.filter(
                Q(name__in=scripts) | Q(tags__overlap=scripts) | Q(id__in=ids),
                script_type=SCRIPT_TYPE.COMMISSIONING)
        for script in qs:
            ScriptResult.objects.create(
                script_set=script_set, status=SCRIPT_STATUS.PENDING,
                script=script, script_name=script.name)

        return script_set

    def create_testing_script_set(self, node, scripts=[]):
        """Create a new testing ScriptSet with ScriptResults.

        Optionally a list of user scripts and tags can be given to create
        ScriptResults for. If None all Scripts tagged 'commissioning' will be
        assumed."""
        # Avoid circular dependencies.
        from metadataserver.models import ScriptResult

        if scripts == []:
            scripts.append('commissioning')

        ids = [
            int(id)
            for id in scripts
            if isinstance(id, int) or id.isdigit()
        ]
        qs = Script.objects.filter(
            Q(name__in=scripts) | Q(tags__overlap=scripts) | Q(id__in=ids),
            script_type=SCRIPT_TYPE.TESTING)

        # A ScriptSet should never be empty. If an empty script set is set as a
        # node's current_testing_script_set the UI will show an empty table and
        # the node-results API will not output any test results.
        if not qs.exists():
            raise NoScriptsFound()

        script_set = self.create(
            node=node, result_type=RESULT_TYPE.TESTING,
            power_state_before_transition=node.power_state)

        for script in qs:
            ScriptResult.objects.create(
                script_set=script_set, status=SCRIPT_STATUS.PENDING,
                script=script, script_name=script.name)

        self._clean_old(node, RESULT_TYPE.TESTING, script_set)
        return script_set

    def create_installation_script_set(self, node):
        """Create a new installation ScriptSet with a ScriptResult."""
        # Avoid circular dependencies.
        from metadataserver.models import ScriptResult

        script_set = self.create(
            node=node, result_type=RESULT_TYPE.INSTALLATION,
            power_state_before_transition=node.power_state)

        # Curtin uploads the installation log using the full path we specify in
        # the preseed.
        ScriptResult.objects.create(
            script_set=script_set, status=SCRIPT_STATUS.PENDING,
            script_name=CURTIN_INSTALL_LOG)

        self._clean_old(node, RESULT_TYPE.INSTALLATION, script_set)
        return script_set


class ScriptSet(CleanSave, Model):

    class Meta(DefaultMeta):
        """Needed for South to recognize this model."""

    objects = ScriptSetManager()

    last_ping = DateTimeField(blank=True, null=True)

    node = ForeignKey('maasserver.Node', on_delete=CASCADE)

    result_type = IntegerField(
        choices=RESULT_TYPE_CHOICES, editable=False,
        default=RESULT_TYPE.COMMISSIONING)

    power_state_before_transition = CharField(
        max_length=10, null=False, blank=False,
        choices=POWER_STATE_CHOICES, default=POWER_STATE.UNKNOWN,
        editable=False)

    def __str__(self):
        return "%s/%s" % (self.node.system_id, self.result_type_name)

    def __iter__(self):
        for script_result in self.scriptresult_set.all():
            yield script_result

    @property
    def result_type_name(self):
        return RESULT_TYPE_CHOICES[self.result_type][1]

    @property
    def status(self):
        qs = self.scriptresult_set.all()
        # The status order below represents the order of precedence.
        for status in (
                SCRIPT_STATUS.RUNNING, SCRIPT_STATUS.PENDING,
                SCRIPT_STATUS.ABORTED, SCRIPT_STATUS.FAILED,
                SCRIPT_STATUS.TIMEDOUT):
            for script_result in qs:
                if (script_result.status == status and
                        status == SCRIPT_STATUS.TIMEDOUT):
                    # A timeout causes the node to go into a failed status
                    # so show the scriptset as failed.
                    return SCRIPT_STATUS.FAILED
                elif script_result.status == status:
                    return status
        return SCRIPT_STATUS.PASSED

    @property
    def status_name(self):
        return SCRIPT_STATUS_CHOICES[self.status][1]

    @property
    def started(self):
        qs = self.scriptresult_set.all()
        if qs.exists():
            return qs.earliest('started').started
        else:
            return None

    @property
    def ended(self):
        qs = self.scriptresult_set.all()
        if not qs.exists():
            return None
        elif qs.filter(ended=None).exists():
            return None
        else:
            return qs.latest('ended').ended

    @property
    def runtime(self):
        if None not in (self.ended, self.started):
            runtime = self.ended - self.started
            return str(runtime - timedelta(microseconds=runtime.microseconds))
        else:
            return ''

    def find_script_result(self, script_result_id=None, script_name=None):
        """Find a script result in the current set."""
        if script_result_id is not None:
            try:
                return self.scriptresult_set.get(id=script_result_id)
            except ObjectDoesNotExist:
                pass
        else:
            for script_result in self:
                if script_result.name == script_name:
                    return script_result
        return None

    def delete(self, force=False, *args, **kwargs):
        if not force and self in {
                self.node.current_commissioning_script_set,
                self.node.current_installation_script_set}:
            # Don't allow deleting current_commissioing_script_set as it is
            # the data set MAAS used to gather hardware information about the
            # node. The current_installation_script_set is only set when a node
            # is deployed. Don't allow it to be deleted as it contains info
            # about the OS deployed.
            raise ValidationError(
                'Unable to delete the current %s script set for node: %s' % (
                    self.result_type_name.lower(), self.node.fqdn))
        elif self == self.node.current_testing_script_set:
            # MAAS uses the current_testing_script_set to know what testing
            # script set should be shown by default in the UI. If an older
            # version exists set the current_testing_script_set to it.
            try:
                previous_script_set = self.node.scriptset_set.filter(
                    result_type=RESULT_TYPE.TESTING)
                previous_script_set = previous_script_set.exclude(id=self.id)
                previous_script_set = previous_script_set.latest('id')
            except ScriptSet.DoesNotExist:
                pass
            else:
                self.node.current_testing_script_set = previous_script_set
                self.node.save(update_fields=['current_testing_script_set'])
        return super().delete(*args, **kwargs)
