from __future__ import with_statement

import mock

from twisted.trial import unittest

from buildbotcustom.misc import _nextIdleSlave, _nextSlave


class TestNextSlaveFuncs(unittest.TestCase):
    def setUp(self):
        self.slaves = slaves = []
        for name in ('s1', 's2', 's3'):
            slave = mock.Mock()
            slave.slave.slavename = name
            slaves.append(slave)

        self.builder = builder = mock.Mock()
        builder.builder_status.buildCache.keys.return_value = []
        builder.slaves = self.slaves

    def test_nextIdleSlave_avail(self):
        """Test that _nextIdleSlave returns a slow slave if enough slow
        slaves are available."""
        func = _nextIdleSlave(1)
        slave = func(self.builder, self.slaves)
        self.assert_(slave)

    def test_nextIdleSlave_unavail(self):
        """Test that _nextIdleSlave returns None if not enough slow
        slaves are available."""
        func = _nextIdleSlave(5)
        slave = func(self.builder, self.slaves)
        self.assert_(slave is None)
