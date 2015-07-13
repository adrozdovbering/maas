# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `FilesystemGroup`."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

import random
import re
from uuid import uuid4

from django.core.exceptions import ValidationError
from maasserver.enum import (
    FILESYSTEM_GROUP_RAID_TYPES,
    FILESYSTEM_GROUP_TYPE,
    FILESYSTEM_TYPE,
)
from maasserver.models.blockdevice import MIN_BLOCK_DEVICE_SIZE
from maasserver.models.filesystemgroup import FilesystemGroup
from maasserver.models.virtualblockdevice import VirtualBlockDevice
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.converters import machine_readable_bytes
from maastesting.matchers import (
    MockCalledOnceWith,
    MockNotCalled,
)
from testtools import ExpectedException
from testtools.matchers import (
    Equals,
    Is,
    Not,
)


class TestFilesystemGroupManager(MAASServerTestCase):
    """Tests for the `FilesystemGroup` manager."""

    def test_get_filesystem_groups_for_on_block_device(self):
        block_device = factory.make_PhysicalBlockDevice()
        filesystem = factory.make_Filesystem(
            fstype=FILESYSTEM_TYPE.LVM_PV, block_device=block_device)
        filesystem_group = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG, filesystems=[filesystem])
        result_filesystem_group_ids = [
            fsgroup.id
            for fsgroup in FilesystemGroup.objects.get_filesystem_groups_for(
                block_device)
        ]
        self.assertItemsEqual(
            [filesystem_group.id], result_filesystem_group_ids)

    def test_get_filesystem_groups_for_on_partition(self):
        block_device = factory.make_PhysicalBlockDevice()
        partition_table = factory.make_PartitionTable(
            block_device=block_device)
        partition = factory.make_Partition(partition_table=partition_table)
        filesystem = factory.make_Filesystem(
            fstype=FILESYSTEM_TYPE.LVM_PV, partition=partition)
        filesystem_group = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG, filesystems=[filesystem])
        result_filesystem_group_ids = [
            fsgroup.id
            for fsgroup in FilesystemGroup.objects.get_filesystem_groups_for(
                block_device)
        ]
        self.assertItemsEqual(
            [filesystem_group.id], result_filesystem_group_ids)

    def test_get_filesystem_groups_for_on_two_partitions(self):
        block_device = factory.make_PhysicalBlockDevice()
        partition_table = factory.make_PartitionTable(
            block_device=block_device)
        partition_one = factory.make_Partition(
            partition_table=partition_table, start_offset=0,
            size=block_device.size / 2)
        partition_two_size = (
            block_device.size - partition_one.size - block_device.block_size)
        partition_two = factory.make_Partition(
            partition_table=partition_table, start_offset=partition_one.size,
            size=partition_two_size)
        filesystem_one = factory.make_Filesystem(
            fstype=FILESYSTEM_TYPE.LVM_PV, partition=partition_one)
        filesystem_two = factory.make_Filesystem(
            fstype=FILESYSTEM_TYPE.LVM_PV, partition=partition_two)
        filesystem_group = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
            filesystems=[filesystem_one, filesystem_two])
        result_filesystem_group_ids = [
            fsgroup.id
            for fsgroup in FilesystemGroup.objects.get_filesystem_groups_for(
                block_device)
        ]
        self.assertItemsEqual(
            [filesystem_group.id], result_filesystem_group_ids)


class TestFilesystemGroup(MAASServerTestCase):
    """Tests for the `FilesystemGroup` model."""

    def test_virtual_device_raises_AttributeError_for_lvm(self):
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG)
        with ExpectedException(AttributeError):
            fsgroup.virtual_device

    def test_virtual_device_returns_VirtualBlockDevice_for_group(self):
        fsgroup = factory.make_FilesystemGroup(
            group_type=factory.pick_enum(
                FILESYSTEM_GROUP_TYPE, but_not=FILESYSTEM_GROUP_TYPE.LVM_VG))
        self.assertEquals(
            VirtualBlockDevice.objects.get(filesystem_group=fsgroup),
            fsgroup.virtual_device)

    def test_get_node_returns_first_filesystem_node(self):
        fsgroup = factory.make_FilesystemGroup()
        self.assertEquals(
            fsgroup.filesystems.first().get_node(), fsgroup.get_node())

    def test_get_node_returns_None_if_no_filesystems(self):
        fsgroup = FilesystemGroup()
        self.assertIsNone(fsgroup.get_node())

    def test_get_size_returns_0_if_lvm_without_filesystems(self):
        fsgroup = FilesystemGroup(group_type=FILESYSTEM_GROUP_TYPE.LVM_VG)
        self.assertEquals(0, fsgroup.get_size())

    def test_get_size_returns_sum_of_all_filesystem_sizes_for_lvm(self):
        node = factory.make_Node()
        total_size = 0
        filesystems = []
        for _ in range(3):
            size = random.randint(
                MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
            total_size += size
            block_device = factory.make_PhysicalBlockDevice(
                node=node, size=size)
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.LVM_PV, block_device=block_device))
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG, filesystems=filesystems)
        self.assertEquals(total_size, fsgroup.get_size())

    def test_get_size_returns_0_if_raid_without_filesystems(self):
        fsgroup = FilesystemGroup(group_type=FILESYSTEM_GROUP_TYPE.RAID_0)
        self.assertEquals(0, fsgroup.get_size())

    def test_get_size_returns_smallest_disk_size_for_raid_0(self):
        node = factory.make_Node()
        small_size = random.randint(
            MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
        large_size = random.randint(small_size + 1, small_size + (10 ** 5))
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(
                    node=node, size=small_size)),
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(
                    node=node, size=large_size)),
        ]
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_0, filesystems=filesystems)
        self.assertEquals(small_size, fsgroup.get_size())

    def test_get_size_returns_smallest_disk_size_for_raid_1(self):
        node = factory.make_Node()
        small_size = random.randint(
            MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
        large_size = random.randint(small_size + 1, small_size + (10 ** 5))
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(
                    node=node, size=small_size)),
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(
                    node=node, size=large_size)),
        ]
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_1, filesystems=filesystems)
        self.assertEquals(small_size, fsgroup.get_size())

    def test_get_size_returns_correct_disk_size_for_raid_4(self):
        node = factory.make_Node()
        small_size = random.randint(
            MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
        other_size = random.randint(small_size + 1, small_size + (10 ** 5))
        number_of_raid_devices = random.randint(2, 9)
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(
                    node=node, size=small_size)),
        ]
        for _ in range(number_of_raid_devices):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID,
                    block_device=factory.make_PhysicalBlockDevice(
                        node=node, size=other_size)))
        # Spares are ignored and not taken into calculation.
        for _ in range(3):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID_SPARE,
                    block_device=factory.make_PhysicalBlockDevice(
                        node=node, size=other_size)))
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_4, filesystems=filesystems)
        self.assertEquals(
            small_size * number_of_raid_devices, fsgroup.get_size())

    def test_get_size_returns_correct_disk_size_for_raid_5(self):
        node = factory.make_Node()
        small_size = random.randint(
            MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
        other_size = random.randint(small_size + 1, small_size + (10 ** 5))
        number_of_raid_devices = random.randint(2, 9)
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(
                    node=node, size=small_size)),
        ]
        for _ in range(number_of_raid_devices):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID,
                    block_device=factory.make_PhysicalBlockDevice(
                        node=node, size=other_size)))
        # Spares are ignored and not taken into calculation.
        for _ in range(3):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID_SPARE,
                    block_device=factory.make_PhysicalBlockDevice(
                        node=node, size=other_size)))
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_5, filesystems=filesystems)
        self.assertEquals(
            small_size * number_of_raid_devices, fsgroup.get_size())

    def test_get_size_returns_correct_disk_size_for_raid_6(self):
        node = factory.make_Node()
        small_size = random.randint(
            MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
        other_size = random.randint(small_size + 1, small_size + (10 ** 5))
        number_of_raid_devices = random.randint(3, 9)
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(
                    node=node, size=small_size)),
        ]
        for _ in range(number_of_raid_devices):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID,
                    block_device=factory.make_PhysicalBlockDevice(
                        node=node, size=other_size)))
        # Spares are ignored and not taken into calculation.
        for _ in range(3):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID_SPARE,
                    block_device=factory.make_PhysicalBlockDevice(
                        node=node, size=other_size)))
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_6, filesystems=filesystems)
        self.assertEquals(
            small_size * (number_of_raid_devices - 1), fsgroup.get_size())

    def test_get_size_returns_0_if_bcache_without_backing(self):
        fsgroup = FilesystemGroup(group_type=FILESYSTEM_GROUP_TYPE.BCACHE)
        self.assertEquals(0, fsgroup.get_size())

    def test_get_size_returns_size_of_backing_device_with_bcache(self):
        node = factory.make_Node()
        backing_size = random.randint(
            MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
        cache_size = random.randint(
            MIN_BLOCK_DEVICE_SIZE, MIN_BLOCK_DEVICE_SIZE ** 2)
        backing_block_device = factory.make_PhysicalBlockDevice(
            node=node, size=backing_size)
        cache_block_device = factory.make_PhysicalBlockDevice(
            node=node, size=cache_size)
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_CACHE,
                block_device=cache_block_device),
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_BACKING,
                block_device=backing_block_device),
        ]
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.BCACHE, filesystems=filesystems)
        self.assertEquals(backing_size, fsgroup.get_size())

    def test_is_lvm_returns_true_when_LVM_VG(self):
        fsgroup = FilesystemGroup(group_type=FILESYSTEM_GROUP_TYPE.LVM_VG)
        self.assertTrue(fsgroup.is_lvm())

    def test_is_lvm_returns_false_when_not_LVM_VG(self):
        fsgroup = FilesystemGroup(
            group_type=factory.pick_enum(
                FILESYSTEM_GROUP_TYPE, but_not=FILESYSTEM_GROUP_TYPE.LVM_VG))
        self.assertFalse(fsgroup.is_lvm())

    def test_is_raid_returns_true_for_all_raid_types(self):
        fsgroup = FilesystemGroup()
        for raid_type in FILESYSTEM_GROUP_RAID_TYPES:
            fsgroup.group_type = raid_type
            self.assertTrue(
                fsgroup.is_raid(),
                "is_raid should return true for %s" % raid_type)

    def test_is_raid_returns_false_for_LVM_VG(self):
        fsgroup = FilesystemGroup(group_type=FILESYSTEM_GROUP_TYPE.LVM_VG)
        self.assertFalse(fsgroup.is_raid())

    def test_is_raid_returns_false_for_BCACHE(self):
        fsgroup = FilesystemGroup(group_type=FILESYSTEM_GROUP_TYPE.BCACHE)
        self.assertFalse(fsgroup.is_raid())

    def test_is_bcache_returns_true_when_BCACHE(self):
        fsgroup = FilesystemGroup(group_type=FILESYSTEM_GROUP_TYPE.BCACHE)
        self.assertTrue(fsgroup.is_bcache())

    def test_is_bcache_returns_false_when_not_BCACHE(self):
        fsgroup = FilesystemGroup(
            group_type=factory.pick_enum(
                FILESYSTEM_GROUP_TYPE, but_not=FILESYSTEM_GROUP_TYPE.BCACHE))
        self.assertFalse(fsgroup.is_bcache())

    def test_can_save_new_filesystem_group_without_filesystems(self):
        fsgroup = FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
            name=factory.make_name("vg"))
        fsgroup.save()
        self.expectThat(fsgroup.id, Not(Is(None)))
        self.expectThat(fsgroup.filesystems.count(), Equals(0))

    def test_cannot_save_without_filesystems(self):
        fsgroup = FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
            name=factory.make_name("vg"))
        fsgroup.save()
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'At least one filesystem must have "
                    "been added.']}")):
            fsgroup.save()

    def test_cannot_save_without_filesystems_from_different_nodes(self):
        filesystems = [
            factory.make_Filesystem(),
            factory.make_Filesystem(),
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'All added filesystems must belong to "
                    "the same node.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
                filesystems=filesystems)

    def test_cannot_save_volume_group_if_invalid_filesystem(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.LVM_PV,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'Volume group can only contain lvm "
                    "physical volumes.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
                filesystems=filesystems)

    def test_can_save_volume_group_if_valid_filesystems(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.LVM_PV,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.LVM_PV,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
        ]
        # Test is that this does not raise an exception.
        factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
            filesystems=filesystems)

    def test_cannot_save_raid_0_with_less_than_2_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 0 must have exactly 2 raid "
                    "devices and no spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_0,
                filesystems=filesystems)

    def test_cannot_save_raid_0_with_more_than_2_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(3)
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 0 must have exactly 2 raid "
                    "devices and no spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_0,
                filesystems=filesystems)

    def test_cannot_save_raid_0_with_spare_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(2)
        ]
        filesystems.append(
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID_SPARE,
                block_device=factory.make_PhysicalBlockDevice(node=node)))
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 0 must have exactly 2 raid "
                    "devices and no spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_0,
                filesystems=filesystems)

    def test_can_save_raid_0_with_exactly_2_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(2)
        ]
        # Test is that this does not raise an exception.
        factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_0,
            filesystems=filesystems)

    def test_cannot_save_raid_1_with_less_than_2_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 1 must have atleast 2 raid "
                    "devices and no spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_1,
                filesystems=filesystems)

    def test_cannot_save_raid_1_with_spare_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(2)
        ]
        filesystems.append(
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID_SPARE,
                block_device=factory.make_PhysicalBlockDevice(node=node)))
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 1 must have atleast 2 raid "
                    "devices and no spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_1,
                filesystems=filesystems)

    def test_can_save_raid_1_with_2_or_more_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(2, 10))
        ]
        # Test is that this does not raise an exception.
        factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_1,
            filesystems=filesystems)

    def test_cannot_save_raid_4_with_less_than_3_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(1, 2))
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 4 must have atleast 3 raid "
                    "devices and any number of spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_4,
                filesystems=filesystems)

    def test_can_save_raid_4_with_3_or_more_raid_devices_and_spares(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(3, 10))
        ]
        for _ in range(random.randint(1, 5)):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID_SPARE,
                    block_device=factory.make_PhysicalBlockDevice(node=node)))
        # Test is that this does not raise an exception.
        factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_4,
            filesystems=filesystems)

    def test_cannot_save_raid_5_with_less_than_3_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(1, 2))
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 5 must have atleast 3 raid "
                    "devices and any number of spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_5,
                filesystems=filesystems)

    def test_can_save_raid_5_with_3_or_more_raid_devices_and_spares(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(3, 10))
        ]
        for _ in range(random.randint(1, 5)):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID_SPARE,
                    block_device=factory.make_PhysicalBlockDevice(node=node)))
        # Test is that this does not raise an exception.
        factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_5,
            filesystems=filesystems)

    def test_cannot_save_raid_6_with_less_than_4_raid_devices(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(1, 3))
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'RAID level 6 must have atleast 4 raid "
                    "devices and any number of spares.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.RAID_6,
                filesystems=filesystems)

    def test_can_save_raid_6_with_4_or_more_raid_devices_and_spares(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.RAID,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(4, 10))
        ]
        for _ in range(random.randint(1, 5)):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.RAID_SPARE,
                    block_device=factory.make_PhysicalBlockDevice(node=node)))
        # Test is that this does not raise an exception.
        factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_6,
            filesystems=filesystems)

    def test_cannot_save_bcache_without_cache(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_BACKING,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'Bcache must contain one cache and one "
                    "backing device.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
                filesystems=filesystems)

    def test_cannot_save_bcache_without_backing(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_CACHE,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
        ]
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'Bcache must contain one cache and one "
                    "backing device.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
                filesystems=filesystems)

    def test_can_save_bcache_with_cache_and_backing(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_CACHE,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_BACKING,
                block_device=factory.make_PhysicalBlockDevice(node=node)),
        ]
        # Test is that this does not raise an exception.
        factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
            filesystems=filesystems)

    def test_cannot_save_bcache_with_multiple_caches(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_CACHE,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(2, 10))
        ]
        filesystems.append(
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_BACKING,
                block_device=factory.make_PhysicalBlockDevice(node=node)))
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'Bcache must contain one cache and one "
                    "backing device.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
                filesystems=filesystems)

    def test_cannot_save_bcache_with_multiple_backings(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_BACKING,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(2, 10))
        ]
        filesystems.append(
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_CACHE,
                block_device=factory.make_PhysicalBlockDevice(node=node)))
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'Bcache must contain one cache and one "
                    "backing device.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
                filesystems=filesystems)

    def test_cannot_save_bcache_with_multiple_caches_and_backings(self):
        node = factory.make_Node()
        filesystems = [
            factory.make_Filesystem(
                fstype=FILESYSTEM_TYPE.BCACHE_BACKING,
                block_device=factory.make_PhysicalBlockDevice(node=node))
            for _ in range(random.randint(2, 10))
        ]
        for _ in range(random.randint(2, 10)):
            filesystems.append(
                factory.make_Filesystem(
                    fstype=FILESYSTEM_TYPE.BCACHE_CACHE,
                    block_device=factory.make_PhysicalBlockDevice(node=node)))
        with ExpectedException(
                ValidationError,
                re.escape(
                    "{'__all__': [u'Bcache must contain one cache and one "
                    "backing device.']}")):
            factory.make_FilesystemGroup(
                group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
                filesystems=filesystems)

    def test_save_doesnt_overwrite_uuid(self):
        uuid = uuid4()
        fsgroup = factory.make_FilesystemGroup(uuid=uuid)
        self.assertEquals('%s' % uuid, fsgroup.uuid)

    def test_save_doesnt_allow_changing_group_type(self):
        fsgroup = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.RAID_0)
        fsgroup.save()
        fsgroup.group_type = FILESYSTEM_GROUP_TYPE.RAID_1
        error = self.assertRaises(ValidationError, fsgroup.save)
        self.assertEquals(
            "Cannot change the group_type of a FilesystemGroup.",
            error.message)

    def test_save_calls_create_or_update_for_when_filesystems_linked(self):
        mock_create_or_update_for = self.patch(
            VirtualBlockDevice.objects, "create_or_update_for")
        filesystem_group = factory.make_FilesystemGroup()
        self.assertThat(
            mock_create_or_update_for, MockCalledOnceWith(filesystem_group))

    def test_save_doesnt_call_create_or_update_for_when_no_filesystems(self):
        mock_create_or_update_for = self.patch(
            VirtualBlockDevice.objects, "create_or_update_for")
        filesystem_group = FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
            name=factory.make_name("vg"))
        filesystem_group.save()
        self.assertThat(
            mock_create_or_update_for, MockNotCalled())

    def test_get_lvm_allocated_size_and_get_lvm_free_space(self):
        """Check get_lvm_allocated_size and get_lvm_free_space methods."""
        backing_volume_size = machine_readable_bytes('10G')
        node = factory.make_Node()
        fsgroup = FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.LVM_VG,
            name=factory.make_name("vg"))
        fsgroup.save()
        for i in range(5):
            block_device = factory.make_BlockDevice(node=node,
                                                    size=backing_volume_size)
            factory.make_Filesystem(filesystem_group=fsgroup,
                                    fstype=FILESYSTEM_TYPE.LVM_PV,
                                    block_device=block_device)
        # Total space should be 50 GB.
        self.assertEqual(fsgroup.get_size(), 50 * 1000 ** 3)

        # Allocate two VirtualBlockDevice's
        factory.make_VirtualBlockDevice(filesystem_group=fsgroup,
                                        size=35 * 1000 ** 3)
        factory.make_VirtualBlockDevice(filesystem_group=fsgroup,
                                        size=5 * 1000 ** 3)

        self.assertEqual(fsgroup.get_lvm_allocated_size(), 40 * 1000 ** 3)
        self.assertEqual(fsgroup.get_lvm_free_space(), 10 * 1000 ** 3)

    def test_get_virtual_block_device_block_size_returns_backing_for_bc(self):
        # This test is not included in the scenario below
        # `TestFilesystemGroupGetVirtualBlockDeviceBlockSize` because it has
        # different logic that doesn't fit in the scenario.
        filesystem_group = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.BCACHE)
        filesystem = filesystem_group.get_bcache_backing_filesystem()
        self.assertEquals(
            filesystem.get_block_size(),
            filesystem_group.get_virtual_block_device_block_size())


class TestFilesystemGroupGetVirtualBlockDevicePrefix(MAASServerTestCase):

    scenarios = [
        (FILESYSTEM_GROUP_TYPE.LVM_VG, {
            "group_type": FILESYSTEM_GROUP_TYPE.LVM_VG,
            "prefix": "lv",
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_0, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_0,
            "prefix": "md",
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_1, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_1,
            "prefix": "md",
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_4, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_4,
            "prefix": "md",
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_5, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_5,
            "prefix": "md",
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_6, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_6,
            "prefix": "md",
            }),
        (FILESYSTEM_GROUP_TYPE.BCACHE, {
            "group_type": FILESYSTEM_GROUP_TYPE.BCACHE,
            "prefix": "bcache",
            }),
    ]

    def test__returns_prefix(self):
        filesystem_group = factory.make_FilesystemGroup(
            group_type=self.group_type)
        self.assertEquals(
            self.prefix, filesystem_group.get_virtual_block_device_prefix())


class TestFilesystemGroupGetVirtualBlockDeviceBlockSize(MAASServerTestCase):

    scenarios = [
        (FILESYSTEM_GROUP_TYPE.LVM_VG, {
            "group_type": FILESYSTEM_GROUP_TYPE.LVM_VG,
            "block_size": 4096,
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_0, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_0,
            "block_size": 512,
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_1, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_1,
            "block_size": 512,
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_4, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_4,
            "block_size": 512,
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_5, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_5,
            "block_size": 512,
            }),
        (FILESYSTEM_GROUP_TYPE.RAID_6, {
            "group_type": FILESYSTEM_GROUP_TYPE.RAID_6,
            "block_size": 512,
            }),
        # For BCACHE see
        # `test_get_virtual_block_device_block_size_returns_backing_for_bc`
        # above.
    ]

    def test__returns_block_size(self):
        filesystem_group = factory.make_FilesystemGroup(
            group_type=self.group_type)
        self.assertEquals(
            self.block_size,
            filesystem_group.get_virtual_block_device_block_size())