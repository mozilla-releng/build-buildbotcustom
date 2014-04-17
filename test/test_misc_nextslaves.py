from __future__ import with_statement

import os
import shutil

import mock

from twisted.trial import unittest
from buildbot.db import dbspec, connector
from buildbot.db.schema.manager import DBSchemaManager

import buildbotcustom.misc
from buildbotcustom.misc import _nextIdleSlave, _nextAWSSlave, \
    _classifyAWSSlaves, _get_pending, J


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

        # Don't check jacuzzi allocations for tests
        J.get_slaves = lambda builder, slaves: slaves

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


class TestNextAWSSlave(unittest.TestCase):
    def setUp(self):
        self.slaves = slaves = []
        for name in ('slave-hw', 'slave-ec2', 'slave-spot-001'):
            slave = mock.Mock()
            slave.slave.slavename = name
            slaves.append(slave)

        self.builder = builder = mock.Mock()
        builder.builder_status.buildCache.keys.return_value = []
        builder.slaves = self.slaves

    def test_classify(self):
        inhouse, ondemand, spot = _classifyAWSSlaves(self.slaves)
        self.assert_(len(inhouse) == 1)
        self.assert_(len(ondemand) == 1)
        self.assert_(len(spot) == 1)
        self.assertEquals(inhouse[0].slave.slavename, "slave-hw")
        self.assertEquals(ondemand[0].slave.slavename, "slave-ec2")
        self.assertEquals(spot[0].slave.slavename, "slave-spot-001")

    def test_nextAWSSlave_inhouse(self):
        """Test that _nextAWSSlave returns the correct slave in different
        situations"""
        f = _nextAWSSlave()
        inhouse, ondemand, spot = _classifyAWSSlaves(self.slaves)

        # Always choose inhouse if available
        self.assertEquals("slave-hw", f(self.builder,
                                        self.slaves).slave.slavename)
        self.assertEquals("slave-hw", f(self.builder,
                                        inhouse + ondemand).slave.slavename)
        self.assertEquals("slave-hw", f(self.builder,
                                        inhouse + spot).slave.slavename)

    def test_nextAWSSlave_AWS(self):
        """Test that we pick between ondemand and spot properly"""
        f = _nextAWSSlave()
        inhouse, ondemand, spot = _classifyAWSSlaves(self.slaves)
        # We need to mock out _get_pending so that we don't have to create a db
        # for these tests
        with mock.patch.object(buildbotcustom.misc, "_get_pending") as \
                _get_pending:
            request = mock.Mock()
            request.submittedAt = 0
            _get_pending.return_value = [request]

            # Sanity check - can't choose any slave if none are available!
            self.assertEquals(None,
                              f(self.builder, []))

            # _get_pending shouldn't get called either
            self.assertEquals(_get_pending.called, 0)

            # _get_pending shouldn't get called if we only have ondemand
            # instances either
            self.assertEquals("slave-ec2",
                              f(self.builder, ondemand).slave.slavename)
            self.assertEquals(_get_pending.called, 0)

            # Spot instances should be preferred
            self.assertEquals("slave-spot-001",
                              f(self.builder, spot + ondemand).slave.slavename)


    def test_nextAWSSlave_AWS_wait(self):
        """Test that we'll wait up to aws_wait for inhouse instances to become
        available"""
        f = _nextAWSSlave(aws_wait=60)
        inhouse, ondemand, spot = _classifyAWSSlaves(self.slaves)
        # We need to mock out _get_pending so that we don't have to create a db
        # for these tests
        with mock.patch.object(buildbotcustom.misc, "_get_pending") as \
                _get_pending:
            # Also need to mock time
            with mock.patch.object(buildbotcustom.misc, "now") as t:
                request = mock.Mock()
                request.submittedAt = 0
                _get_pending.return_value = [request]

                # at t=1, we shouldn't use an ondemand or spot intance
                t.return_value = 1
                self.assertEquals(None, f(self.builder, spot + ondemand))

                # at t=61, we shoue use an ondemand or spot intance
                t.return_value = 61
                self.assertEquals("slave-spot-001",
                                  f(self.builder,
                                    spot + ondemand).slave.slavename)
                self.assertEquals("slave-ec2",
                                  f(self.builder,
                                    ondemand).slave.slavename)

    def test_nextAWSSlave_noRequests(self):
        """Test that everything works if there are no pending requests to
        getRetries"""
        f = _nextAWSSlave()
        inhouse, ondemand, spot = _classifyAWSSlaves(self.slaves)
        # We need to mock out _get_pending so that we don't have to create a db
        # for these tests
        with mock.patch.object(buildbotcustom.misc, "_get_pending") as \
                _get_pending:
            _get_pending.return_value = []

            # Sanity check - can't choose any slave if none are available!
            self.assertEquals(None,
                              f(self.builder, []))

            # _get_pending shouldn't get called either
            self.assertEquals(_get_pending.called, 0)

            # _get_pending shouldn't get called if we only have ondemand
            # instances either
            self.assertEquals("slave-ec2",
                              f(self.builder, ondemand).slave.slavename)
            self.assertEquals(_get_pending.called, 0)

            # Spot instances should be preferred if there are no retries
            self.assertEquals("slave-spot-001",
                              f(self.builder, spot + ondemand).slave.slavename)


class TestGetPending(unittest.TestCase):
    basedir = "test_misc_nextslaves"

    def setUp(self):
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)
        os.makedirs(self.basedir)
        spec = dbspec.DBSpec.from_url("sqlite:///state.sqlite", self.basedir)
        # For testing against mysql, uncomment this
        #spec = dbspec.DBSpec.from_url("mysql://buildbot@localhost/buildbot_schedulers", self.basedir)
        manager = DBSchemaManager(spec, self.basedir)
        manager.upgrade()

        self.dbc = connector.DBConnector(spec)
        self.dbc.start()

    def tearDown(self):
        self.dbc.stop()
        shutil.rmtree(self.basedir)

    def test_get_pending(self):
        # Get a db transaction object so that getRetries' stack walking can
        # find it!
        t = self.dbc.get_sync_connection().cursor()

        builder = mock.Mock()
        builder._getBuildable.return_value = []
        builder.db = self.dbc

        self.assertEquals(_get_pending(builder), ([]))
