from __future__ import with_statement
import time
import tempfile

import mock

from twisted.trial import unittest

import buildbotcustom.misc
from buildbotcustom.misc import _nextSlowIdleSlave, _nextL10nSlave,\
        _nextFastSlave, _nextFastReservedSlave, _nextSlowSlave,\
        setReservedFileName

class TestNextSlaveFuncs(unittest.TestCase):
    def setUp(self):
        # Reset these each time
        buildbotcustom.misc.fastRegexes = ['fast']
        buildbotcustom.misc.nReservedFastSlaves = 0
        buildbotcustom.misc.nReservedSlowSlaves = 0

        # Prevent looking for reserved slaves file
        buildbotcustom.misc._checkedReservedSlaveFile = time.time()

        self.slaves = slaves = []
        for name in ('fast1', 'fast2', 'fast3', 'slow1', 'slow2', 'slow3'):
            slave = mock.Mock()
            slave.slave.slavename = name
            slaves.append(slave)
        self.slow_slaves = [s for s in self.slaves if "slow" in s.slave.slavename]
        self.fast_slaves = [s for s in self.slaves if "fast" in s.slave.slavename]

        self.builder = builder = mock.Mock()
        builder.builder_status.buildCache.keys.return_value = []
        builder.slaves = self.slaves

    def test_nextFastSlave_AllAvail(self):
        """Test that _nextFastSlave and _nextFastReservedSlave return a fast
        slave when all slaves are available."""
        for func in _nextFastReservedSlave, _nextFastSlave:
            slave = func(self.builder, self.slaves, only_fast=True)
            self.assert_(slave.slave.slavename.startswith("fast"))

    def test_nextFastSlave_OnlySlowAvail(self):
        """Test that _nextFastSlave and _nextFastReservedSlave return None
        slave when only slow slaves are available, and only_fast is True."""
        for func in _nextFastReservedSlave, _nextFastSlave:
            slave = func(self.builder, self.slow_slaves, only_fast=True)
            self.assert_(slave is None)

    def test_nextFastSlave_OnlySlowAvail_notOnlyFast(self):
        """Test that _nextFastSlave and _nextFastReservedSlave return a slow
        slave when only slow slaves are available and only_fast is False."""
        for func in _nextFastReservedSlave, _nextFastSlave:
            slave = func(self.builder, self.slow_slaves, only_fast=False)
            self.assert_(slave.slave.slavename.startswith("slow"))

    def test_nextFastReservedSlave_reserved(self):
        """Test that _nextFastReservedSlave returns a fast slave if there's one
        reserved."""
        buildbotcustom.misc.nReservedFastSlaves = 1

        # Only one fast slave available
        available_slaves = [s for s in self.slaves if s.slave.slavename == 'fast2']
        slave = _nextFastReservedSlave(self.builder, available_slaves)
        self.assert_(slave.slave.slavename == "fast2")

    def test_nextFastSlave_reserved(self):
        """Test that _nextFastSlave returns None if there's one slave
        reserved."""
        buildbotcustom.misc.nReservedFastSlaves = 1

        # Only one fast slave available
        available_slaves = [s for s in self.slaves if s.slave.slavename == 'fast2']
        slave = _nextFastSlave(self.builder, available_slaves)
        self.assert_(slave is None)

    def test_nextFastSlave_allslow(self):
        """Test that _nextFastSlave works if the builder is configured with
        just slow slaves. This handles the case for platforms that don't have a
        fast/slow distinction."""
        buildbotcustom.misc.nReservedFastSlaves = 1
        self.builder.slaves = self.slow_slaves

        slave = _nextFastSlave(self.builder, self.slow_slaves, only_fast=True)
        self.assert_(slave.slavename.startswith('slow'))

    def test_nextSlowSlave(self):
        """Test that _nextSlowSlave returns a slow slave if one is available."""
        slave = _nextSlowSlave(self.builder, self.slaves)
        self.assert_(slave.slave.slavename.startswith("slow"))

    def test_nextSlowSlave_OnlyFastAvail(self):
        """Test that _nextSlowSlave returns a fast slave if no slow slaves are
        available."""
        slave = _nextSlowSlave(self.builder, self.fast_slaves)
        self.assert_(slave.slave.slavename.startswith("fast"))

    def test_nextSlowIdleSlave_avail(self):
        """Test that _nextSlowIdleSlave returns a slow slave if enough slow
        slaves are available."""
        func = _nextSlowIdleSlave(1)
        slave = func(self.builder, self.slaves)
        self.assert_(slave.slave.slavename.startswith("slow"))

    def test_nextSlowIdleSlave_unavail(self):
        """Test that _nextSlowIdleSlave returns None if not enough slow
        slaves are available."""
        func = _nextSlowIdleSlave(5)
        slave = func(self.builder, self.slaves)
        self.assert_(slave is None)

    def test_nextL10nSlave_avail(self):
        """Test that _nextL10nSlave returns a slow slave if the first slow
        slave is available."""
        func = _nextL10nSlave(1)
        slave = func(self.builder, self.slaves)
        self.assert_(slave.slave.slavename == 'slow1')

    def test_nextL10nSlave_unavail(self):
        """Test that _nextL10nSlave returns None if the first slow slave is not
        available."""
        func = _nextL10nSlave(1)
        available_slaves = [s for s in self.slaves if s.slave.slavename != 'slow1']
        slave = func(self.builder, available_slaves)
        self.assert_(slave is None)

    def test_update_reserved(self):
        """Test that updates to the reserved file are obeyed, and that calls to
        the _nextFast functions pick it up."""
        reservedFile = tempfile.NamedTemporaryFile()
        buildbotcustom.misc._checkedReservedSlaveFile = 0
        # Need to fake out time.time
        with mock.patch.object(time, 'time') as time_method:
            setReservedFileName(reservedFile.name)
            time_method.return_value = 0
            self.assertEquals(buildbotcustom.misc.nReservedFastSlaves, 0)

            # Only one fast slave available, but none are reserved yet
            available_slaves = [s for s in self.slaves if s.slave.slavename == 'fast2']
            slave = _nextFastSlave(self.builder, available_slaves)
            self.assert_(slave.slave.slavename == 'fast2')

            # Reserve 1 slave
            reservedFile.write('1')
            reservedFile.flush()
            time_method.return_value = 61

            # Only one fast slave available, but 1 is reserved
            available_slaves = [s for s in self.slaves if s.slave.slavename == 'fast2']

            # Check that the regular function doesn't get it
            slave = _nextFastSlave(self.builder, available_slaves, only_fast=True)
            self.assertEquals(buildbotcustom.misc.nReservedFastSlaves, 1)
            self.assert_(slave is None)

            # But our reserved function now does
            slave = _nextFastReservedSlave(self.builder, available_slaves, only_fast=True)
            self.assert_(slave.slave.slavename == 'fast2')

    def test_update_reserved_blank(self):
        """Test that updates to the reserved file are obeyed, and that calls to
        the _nextFast functions pick it up."""
        reservedFile = tempfile.NamedTemporaryFile()
        reservedFile.write('5')
        reservedFile.flush()
        buildbotcustom.misc._checkedReservedSlaveFile = 0
        # Need to fake out time.time
        with mock.patch.object(time, 'time') as time_method:
            setReservedFileName(reservedFile.name)
            time_method.return_value = 61
            self.assertEquals(buildbotcustom.misc.nReservedFastSlaves, 0)

            # Only one fast slave available, but all are reserved yet
            available_slaves = [s for s in self.slaves if s.slave.slavename == 'fast2']
            slave = _nextFastSlave(self.builder, available_slaves)
            self.assert_(slave is None)
            self.assertEquals(buildbotcustom.misc.nReservedFastSlaves, 5)

            # Empty out reserved slaves file
            reservedFile.seek(0)
            reservedFile.write('')
            reservedFile.truncate()
            reservedFile.flush()
            time_method.return_value = buildbotcustom.misc._checkedReservedSlaveFile + 61

            # Only one fast slave available, but none are reserved
            available_slaves = [s for s in self.slaves if s.slave.slavename == 'fast2']

            # Check that the regular function gets it
            slave = _nextFastSlave(self.builder, available_slaves, only_fast=True)
            self.assertEquals(buildbotcustom.misc.nReservedFastSlaves, 0)
            self.assert_(slave.slave.slavename == 'fast2')
