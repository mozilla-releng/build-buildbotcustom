from __future__ import with_statement

import mock

from twisted.trial import unittest

import buildbotcustom.misc
from buildbotcustom.misc import _nextSlowIdleSlave, _nextL10nSlave,\
    _nextFastSlave, _nextSlowSlave


class TestNextSlaveFuncs(unittest.TestCase):
    def setUp(self):
        # Reset these each time
        buildbotcustom.misc.fastRegexes = ['fast']

        self.slaves = slaves = []
        for name in ('fast1', 'fast2', 'fast3', 'slow1', 'slow2', 'slow3'):
            slave = mock.Mock()
            slave.slave.slavename = name
            slaves.append(slave)
        self.slow_slaves = [
            s for s in self.slaves if "slow" in s.slave.slavename]
        self.fast_slaves = [
            s for s in self.slaves if "fast" in s.slave.slavename]

        self.builder = builder = mock.Mock()
        builder.builder_status.buildCache.keys.return_value = []
        builder.slaves = self.slaves

    def test_nextFastSlave_AllAvail(self):
        """Test that _nextFastSlave returns a fast
        slave when all slaves are available."""
        slave = _nextFastSlave(self.builder, self.slaves, only_fast=True)
        self.assert_(slave.slave.slavename.startswith("fast"))

    def test_nextFastSlave_OnlySlowAvail(self):
        """Test that _nextFastSlave returns None slave when only slow slaves
        are available, and only_fast is True."""
        slave = _nextFastSlave(self.builder, self.slow_slaves, only_fast=True)
        self.assert_(slave is None)

    def test_nextFastSlave_OnlySlowAvail_notOnlyFast(self):
        """Test that _nextFastSlave and returns a slow slave when only slow
        slaves are available and only_fast is False."""
        slave = _nextFastSlave(self.builder, self.slow_slaves, only_fast=False)
        self.assert_(slave.slave.slavename.startswith("slow"))

    def test_nextFastSlave_allslow(self):
        """Test that _nextFastSlave works if the builder is configured with
        just slow slaves. This handles the case for platforms that don't have a
        fast/slow distinction."""
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
        available_slaves = [
            s for s in self.slaves if s.slave.slavename != 'slow1']
        slave = func(self.builder, available_slaves)
        self.assert_(slave is None)
