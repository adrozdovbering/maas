# Copyright 2014-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

__all__ = [
    'import_images',
    'main',
    'main_with_services',
    'make_arg_parser',
    ]

from argparse import ArgumentParser
import errno
from io import StringIO
import os
from textwrap import dedent

from provisioningserver.boot import BootMethodRegistry
from provisioningserver.boot.tftppath import list_boot_images
from provisioningserver.config import (
    BootSources,
    ClusterConfiguration,
)
from provisioningserver.events import (
    EVENT_TYPES,
    try_send_rack_event,
)
from provisioningserver.import_images.cleanup import (
    cleanup_snapshots_and_cache,
)
from provisioningserver.import_images.download_descriptions import (
    download_all_image_descriptions,
)
from provisioningserver.import_images.download_resources import (
    download_all_boot_resources,
)
from provisioningserver.import_images.helpers import maaslog
from provisioningserver.import_images.keyrings import write_all_keyrings
from provisioningserver.import_images.product_mapping import map_products
from provisioningserver.path import get_path
from provisioningserver.rpc import getRegionClient
from provisioningserver.service_monitor import service_monitor
from provisioningserver.utils import sudo
from provisioningserver.utils.fs import (
    atomic_symlink,
    atomic_write,
    read_text_file,
    tempdir,
)
from provisioningserver.utils.shell import (
    call_and_check,
    ExternalProcessError,
)
from twisted.internet.defer import inlineCallbacks
from twisted.python.filepath import FilePath


class NoConfigFile(Exception):
    """Raised when the config file for the script doesn't exist."""


def tgt_entry(osystem, arch, subarch, release, label, image):
    """Generate tgt target used to commission arch/subarch with release

    Tgt target used to commission arch/subarch machine with a specific Ubuntu
    release should have the following name: ephemeral-arch-subarch-release.
    This function creates target description in a format used by tgt-admin.
    It uses arch, subarch and release to generate target name and image as
    a path to image file which should be shared. Tgt target is marked as
    read-only. Tgt target has 'allow-in-use' option enabled because this
    script actively uses hardlinks to do image management and root images
    in different folders may point to the same inode. Tgt doesn't allow us to
    use the same inode for different tgt targets (even read-only targets which
    looks like a bug to me) without this option enabled.

    :param osystem: Operating System name we generate tgt target for
    :param arch: Architecture name we generate tgt target for
    :param subarch: Subarchitecture name we generate tgt target for
    :param release: Ubuntu release we generate tgt target for
    :param label: The images' label
    :param image: Path to the image which should be shared via tgt/iscsi
    :return Tgt entry which can be written to tgt-admin configuration file
    """
    prefix = 'iqn.2004-05.com.ubuntu:maas'
    target_name = 'ephemeral-%s-%s-%s-%s-%s' % (
        osystem,
        arch,
        subarch,
        release,
        label
        )
    entry = dedent("""\
        <target {prefix}:{target_name}>
            readonly 1
            allow-in-use yes
            backing-store "{image}"
            driver iscsi
        </target>
        """).format(prefix=prefix, target_name=target_name, image=image)
    return entry


def link_bootloaders(destination):
    """Link the all the required file from each bootloader method.
    :param destination: Directory where the loaders should be stored.
    """
    for _, boot_method in BootMethodRegistry:
        try:
            boot_method.link_bootloader(destination)
        except BaseException:
            msg = "Unable to link the %s bootloader.", boot_method.name
            try_send_rack_event(EVENT_TYPES.RACK_IMPORT_ERROR, msg)
            maaslog.error(msg)


def make_arg_parser(doc):
    """Create an `argparse.ArgumentParser` for this script."""

    parser = ArgumentParser(description=doc)
    parser.add_argument(
        '--sources-file', action="store", required=True,
        help=(
            "Path to YAML file defining import sources. "
            "See this script's man page for a description of "
            "that YAML file's format."
        )
    )
    return parser


def compose_targets_conf(snapshot_path):
    """Produce the contents of a snapshot's tgt conf file.

    :param snapshot_path: Filesystem path to a snapshot of current upstream
        boot resources.
    :return: Contents for a `targets.conf` file.
    :rtype: bytes
    """
    # Use a set to make sure we don't register duplicate entries in tgt.
    entries = set()
    for item in list_boot_images(snapshot_path):
        osystem = item['osystem']
        arch = item['architecture']
        subarch = item['subarchitecture']
        release = item['release']
        label = item['label']
        entries.add((osystem, arch, subarch, release, label))
    tgt_entries = []
    for osystem, arch, subarch, release, label in sorted(entries):
        base_path = os.path.join(
            snapshot_path, osystem, arch, subarch,
            release, label)
        root_image = os.path.join(base_path, 'root-image')
        if os.path.isfile(root_image):
            entry = tgt_entry(
                osystem, arch, subarch, release, label, root_image)
            tgt_entries.append(entry)
        squashfs_image = os.path.join(base_path, 'squashfs')
        if os.path.isfile(squashfs_image):
            entry = tgt_entry(
                osystem, arch, subarch, release, label, squashfs_image)
            tgt_entries.append(entry)
    text = ''.join(tgt_entries)
    return text.encode('utf-8')


def meta_contains(storage, content):
    """Does the `maas.meta` file match `content`?

    If the file's contents match the latest data, there is no need to update.

    The file's timestamp is also updated to now to reflect the last time
    that this import was run.
    """
    current_meta = os.path.join(storage, 'current', 'maas.meta')
    exists = os.path.isfile(current_meta)
    if exists:
        # Touch file to the current timestamp so that the last time this
        # import ran can be determined.
        os.utime(current_meta, None)
    return exists and content == read_text_file(current_meta)


def update_current_symlink(storage, latest_snapshot):
    """Symlink `latest_snapshot` as the "current" snapshot."""
    atomic_symlink(latest_snapshot, os.path.join(storage, 'current'))


def write_snapshot_metadata(snapshot, meta_file_content):
    """Write "maas.meta" file.

    :param meta_file_content: A Unicode string (`str`) containing JSON using
        only ASCII characters.
    """
    meta_file = os.path.join(snapshot, 'maas.meta')
    atomic_write(meta_file_content.encode("ascii"), meta_file, mode=0o644)


def write_targets_conf(snapshot):
    """Write "maas.tgt" file."""
    targets_conf = os.path.join(snapshot, 'maas.tgt')
    targets_conf_content = compose_targets_conf(snapshot)
    atomic_write(targets_conf_content, targets_conf, mode=0o644)


def update_targets_conf(snapshot):
    """Runs tgt-admin to update the new targets from "maas.tgt"."""
    # Ensure that tgt is running before tgt-admin is used.
    service_monitor.ensureService("tgt").wait(30)

    # Update the tgt config.
    targets_conf = os.path.join(snapshot, 'maas.tgt')

    # The targets_conf may not exist in the event the BootSource is broken
    # and images havn't been imported yet. This fixes LP:1655721
    if not os.path.exists(targets_conf):
        return

    try:
        call_and_check(sudo([
            get_path('/usr/sbin/tgt-admin'),
            '--conf', targets_conf,
            '--update', 'ALL',
            ]))
    except ExternalProcessError as e:
        msg = "Unable to update TGT config: %s" % e
        try_send_rack_event(EVENT_TYPES.RACK_IMPORT_WARNING, msg)
        maaslog.warning(msg)


def read_sources(sources_yaml):
    """Read boot resources config file.

    :param sources_yaml: Path to a YAML file containing a list of boot
        resource definitions.
    :return: A dict representing the boot-resources configuration.
    :raise NoConfigFile: If the configuration file was not present.
    """
    # The config file is required.  We do not fall back to defaults if it's
    # not there.
    try:
        return BootSources.load(filename=sources_yaml)
    except IOError as ex:
        if ex.errno == errno.ENOENT:
            # No config file. We have helpful error output for this.
            raise NoConfigFile(ex)
        else:
            # Unexpected error.
            raise


def parse_sources(sources_yaml):
    """Given a YAML `config` string, return a `BootSources` for it."""
    return BootSources.parse(StringIO(sources_yaml))


def update_iscsi_targets(snapshot_path):
    maaslog.info("Updating boot image iSCSI targets.")
    update_targets_conf(snapshot_path)


def import_images(sources):
    """Import images.  Callable from the command line.

    :param config: An iterable of dicts representing the sources from
        which boot images will be downloaded.
    """
    if len(sources) == 0:
        msg = "Can't import: region did not provide a source."
        try_send_rack_event(EVENT_TYPES.RACK_IMPORT_WARNING, msg)
        maaslog.warning(msg)
        return False

    msg = "Starting rack boot image import"
    maaslog.info(msg)
    try_send_rack_event(EVENT_TYPES.RACK_IMPORT_INFO, msg)

    with ClusterConfiguration.open() as config:
        storage = FilePath(config.tftp_root).parent().path

    with tempdir('keyrings') as keyrings_path:
        # XXX: Band-aid to ensure that the keyring_data is bytes. Future task:
        # try to figure out why this sometimes happens.
        for source in sources:
            if ('keyring_data' in source and
                    not isinstance(source['keyring_data'], bytes)):
                source['keyring_data'] = source['keyring_data'].encode('utf-8')

        # We download the keyrings now  because we need them for both
        # download_all_image_descriptions() and
        # download_all_boot_resources() later.
        sources = write_all_keyrings(keyrings_path, sources)

        # The region produces a SimpleStream which is similar, but not
        # identical to the actual SimpleStream. These differences cause
        # validation to fail. So grab everything from the region and trust it
        # did proper filtering before the rack.
        image_descriptions = download_all_image_descriptions(
            sources, validate_products=False)
        if image_descriptions.is_empty():
            update_iscsi_targets(os.path.join(storage, 'current'))
            msg = (
                "Finished importing boot images, the region does not have "
                "any boot images available.")
            try_send_rack_event(EVENT_TYPES.RACK_IMPORT_WARNING, msg)
            maaslog.warning(msg)
            return False

        meta_file_content = image_descriptions.dump_json()
        if meta_contains(storage, meta_file_content):
            update_iscsi_targets(os.path.join(storage, 'current'))
            maaslog.info(
                "Finished importing boot images, the region does not "
                "have any new images.")
            try_send_rack_event(EVENT_TYPES.RACK_IMPORT_INFO, msg)
            maaslog.info(msg)
            return False

        product_mapping = map_products(image_descriptions)

        try:
            snapshot_path = download_all_boot_resources(
                sources, storage, product_mapping)
        except Exception as e:
            try_send_rack_event(
                EVENT_TYPES.RACK_IMPORT_ERROR,
                "Unable to import boot images: %s" % e)
            update_iscsi_targets(os.path.join(storage, 'current'))
            maaslog.error(
                "Unable to import boot images; cleaning up failed snapshot "
                "and cache.")
            # Cleanup snapshots and cache since download failed.
            cleanup_snapshots_and_cache(storage)
            raise

    maaslog.info("Writing boot image metadata and iSCSI targets.")
    write_snapshot_metadata(snapshot_path, meta_file_content)
    write_targets_conf(snapshot_path)

    maaslog.info("Linking boot images snapshot %s" % snapshot_path)
    link_bootloaders(snapshot_path)

    # If we got here, all went well.  This is now truly the "current" snapshot.
    update_current_symlink(storage, snapshot_path)

    update_iscsi_targets(snapshot_path)

    # Now cleanup the old snapshots and cache.
    maaslog.info('Cleaning up old snapshots and cache.')
    cleanup_snapshots_and_cache(storage)

    # Import is now finished.
    msg = "Finished importing boot images."
    maaslog.info(msg)
    try_send_rack_event(EVENT_TYPES.RACK_IMPORT_INFO, msg)
    return True


def main(args):
    """Entry point for the command-line import script.

    :param args: Command-line arguments as parsed by the `ArgumentParser`
        returned by `make_arg_parser`.
    :raise NoConfigFile: If a config file is specified but doesn't exist.
    """
    sources = read_sources(args.sources_file)
    import_images(sources=sources)


def main_with_services(args):
    """The *real* entry point for the command-line import script.

    This sets up the necessary RPC services before calling `main`, then clears
    up behind itself.

    :param args: Command-line arguments as parsed by the `ArgumentParser`
        returned by `make_arg_parser`.
    :raise NoConfigFile: If a config file is specified but doesn't exist.

    """
    from sys import stderr
    import traceback

    from provisioningserver import services
    from provisioningserver.rpc.clusterservice import ClusterClientService
    from provisioningserver.rpc.exceptions import NoConnectionsAvailable
    from provisioningserver.utils.twisted import retries, pause
    from twisted.internet import reactor
    from twisted.internet.threads import deferToThread

    @inlineCallbacks
    def start_services():
        rpc_service = ClusterClientService(reactor)
        rpc_service.setName("rpc")
        rpc_service.setServiceParent(services)

        yield services.startService()

        for elapsed, remaining, wait in retries(15, 1, reactor):
            try:
                yield getRegionClient()
            except NoConnectionsAvailable:
                yield pause(wait, reactor)
            else:
                break
        else:
            print("Can't connect to the region.", file=stderr)
            raise SystemExit(1)

    @inlineCallbacks
    def stop_services():
        yield services.stopService()

    exit_codes = {0}

    @inlineCallbacks
    def run_main():
        try:
            yield start_services()
            try:
                yield deferToThread(main, args)
            finally:
                yield stop_services()
        except SystemExit as se:
            exit_codes.add(se.code)
        except:
            exit_codes.add(2)
            print("Failed to import boot resources", file=stderr)
            traceback.print_exc()
        finally:
            reactor.callLater(0, reactor.stop)

    reactor.callWhenRunning(run_main)
    reactor.run()

    exit_code = max(exit_codes)
    raise SystemExit(exit_code)
