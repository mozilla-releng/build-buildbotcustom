# Tests for the aggregating scheduler
import os
import shutil
import json

from buildbot.db import dbspec, connector
from buildbot.db.schema.manager import DBSchemaManager
from twisted.trial import unittest

import mock

from buildbotcustom.scheduler import AggregatingScheduler


class TestAggregatingScheduler(unittest.TestCase):
    basedir = "test_scheduler_aggregating"

    def setUp(self):
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)
        os.makedirs(self.basedir)
        spec = dbspec.DBSpec.from_url("sqlite:///state.sqlite", self.basedir)
        manager = DBSchemaManager(spec, self.basedir)
        manager.upgrade()

        self.dbc = connector.DBConnector(spec)
        self.dbc.start()
        self._patcher = mock.patch("buildbotcustom.scheduler.now")
        self._time = self._patcher.start()
        self._time.return_value = 123

    def tearDown(self):
        self.dbc.stop()
        shutil.rmtree(self.basedir)
        self._patcher.stop()

    def testCreate(self):
        s = AggregatingScheduler(name='s1', branch='b1', builderNames=['d1', 'd2'], upstreamBuilders=['u1', 'u2'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        d = self.dbc.addSchedulers([s])

        def checkState(_):
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u1", "u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 123, "lastReset": 123})

        d.addCallback(checkState)
        return d

    def testTriggerDownstreams(self):
        # Test of the basic functionality. Do we fire our downstream builders
        # when our upstream finishes?
        s = AggregatingScheduler(name='s1', branch='b1', builderNames=['d1', 'd2'], upstreamBuilders=['u1'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        d = self.dbc.addSchedulers([s])

        def check(_):
            requests = self.dbc.runQueryNow("SELECT * FROM buildrequests")
            self.assertEquals(len(requests), 0)
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u1"], "upstreamBuilders": ["u1"], "lastCheck": 123, "lastReset": 123})
        d.addCallback(check)

        def addFinishedBuild(_):
            self.dbc.runQueryNow("""
                    INSERT into buildrequests
                    (buildsetid, buildername, complete, complete_at, submitted_at, results) VALUES
                    (0, 'u1', 1, 124, 0, 0)
            """)
            # Now run the scheduler
            self._time.return_value = 200
            return s.run()
        d.addCallback(addFinishedBuild)

        def checkRequests(_):
            requests = self.dbc.runQueryNow("SELECT buildername FROM buildrequests WHERE complete=0")
            self.assertEquals(len(requests), 2)
            self.assertEquals(sorted([r[0] for r in requests]), ['d1', 'd2'])
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            # We use the time of the last completed build as our lastCheck time
            self.assertEquals(state, {"remainingBuilders": ["u1"], "upstreamBuilders": ["u1"], "lastCheck": 124, "lastReset": 200})
        d.addCallback(checkRequests)

        return d

    def testMultipleUpstreams(self):
        # Test of more complicated functionality. Do we fire our downstream builders
        # when all of our upstreams finish?
        # We'll set up 2 upstreams, and finish 3 builds, the middle one will
        # fail
        s = AggregatingScheduler(name='s1', branch='b1', builderNames=['d1', 'd2'], upstreamBuilders=['u1', 'u2'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        d = self.dbc.addSchedulers([s])

        def check(_):
            requests = self.dbc.runQueryNow("SELECT * FROM buildrequests")
            self.assertEquals(len(requests), 0)
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u1", "u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 123, "lastReset": 123})
        d.addCallback(check)

        def addFinishedBuild1(_):
            self.dbc.runQueryNow("""
                    INSERT into buildrequests
                    (buildsetid, buildername, complete, complete_at, submitted_at, results) VALUES
                    (0, 'u1', 1, 124, 0, 0)
            """)
            # Now run the scheduler
            self._time.return_value = 200
            return s.run()
        d.addCallback(addFinishedBuild1)

        def addFinishedBuild2(_):
            # Check that we noticed the first build completed
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 124, "lastReset": 123})

            # Add the 2nd job, which fails
            self.dbc.runQueryNow("""
                    INSERT into buildrequests
                    (buildsetid, buildername, complete, complete_at, submitted_at, results) VALUES
                    (0, 'u2', 1, 125, 0, 5)
            """)

            # Now run the scheduler
            self._time.return_value = 201
            return s.run()
        d.addCallback(addFinishedBuild2)

        def addFinishedBuild3(_):
            # Check that we ignored the second failed build
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 124, "lastReset": 123})

            # Add the 3rd job, which succeeds
            self.dbc.runQueryNow("""
                    INSERT into buildrequests
                    (buildsetid, buildername, complete, complete_at, submitted_at, results) VALUES
                    (0, 'u2', 1, 126, 0, 0)
            """)

            # Now run the scheduler
            self._time.return_value = 202
            return s.run()
        d.addCallback(addFinishedBuild3)

        def checkRequests(_):
            requests = self.dbc.runQueryNow("SELECT buildername FROM buildrequests WHERE complete=0")
            self.assertEquals(len(requests), 2)
            self.assertEquals(sorted([r[0] for r in requests]), ['d1', 'd2'])
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            # We use the time of the last completed build as our lastCheck time
            self.assertEquals(state, {"remainingBuilders": ["u1", "u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 126, "lastReset": 202})
        d.addCallback(checkRequests)

        return d

    def testFinishLag(self):
        # Make sure we can handle builds finishing slightly out of order in the
        # DB. c.f. bug 811708
        s = AggregatingScheduler(name='s1', branch='b1', builderNames=['d1', 'd2'], upstreamBuilders=['u1', 'u2'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        d = self.dbc.addSchedulers([s])

        def check(_):
            requests = self.dbc.runQueryNow("SELECT * FROM buildrequests")
            self.assertEquals(len(requests), 0)
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u1", "u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 123, "lastReset": 123})
        d.addCallback(check)

        def addFinishedBuild1(_):
            self.dbc.runQueryNow("""
                    INSERT into buildrequests
                    (buildsetid, buildername, complete, complete_at, submitted_at, results) VALUES
                    (0, 'u1', 1, 130, 0, 0)
            """)
            # Now run the scheduler
            self._time.return_value = 130
            return s.run()
        d.addCallback(addFinishedBuild1)

        def addFinishedBuild2(_):
            # Check that we noticed the first build completed
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 130, "lastReset": 123})

            # Add the 2nd job which finishes before the 1st
            self.dbc.runQueryNow("""
                    INSERT into buildrequests
                    (buildsetid, buildername, complete, complete_at, submitted_at, results) VALUES
                    (0, 'u2', 1, 129, 0, 0)
            """)

            # Now run the scheduler
            self._time.return_value = 131
            return s.run()
        d.addCallback(addFinishedBuild2)

        def checkRequests(_):
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            # We use the time of the last completed build as our lastCheck time
            self.assertEquals(state, {"remainingBuilders": ["u1", "u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 130, "lastReset": 131})

            requests = self.dbc.runQueryNow("SELECT buildername FROM buildrequests WHERE complete=0")
            self.assertEquals(len(requests), 2)
            self.assertEquals(sorted([r[0] for r in requests]), ['d1', 'd2'])
        d.addCallback(checkRequests)

        return d

    def testTrigger(self):
        # Test that we can reset the state of the scheduler via trigger()
        s = AggregatingScheduler(name='s1', branch='b1', builderNames=['d1', 'd2'], upstreamBuilders=['u1', 'u2'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        d = self.dbc.addSchedulers([s])

        def check(_):
            requests = self.dbc.runQueryNow("SELECT * FROM buildrequests")
            self.assertEquals(len(requests), 0)
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u1", "u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 123, "lastReset": 123})
        d.addCallback(check)

        def addFinishedBuild1(_):
            self.dbc.runQueryNow("""
                    INSERT into buildrequests
                    (buildsetid, buildername, complete, complete_at, submitted_at, results) VALUES
                    (0, 'u1', 1, 124, 0, 0)
            """)
            # Now run the scheduler
            self._time.return_value = 200
            return s.run()
        d.addCallback(addFinishedBuild1)

        def doTrigger(_):
            # Check that we noticed the first build completed
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 124, "lastReset": 123})

            self._time.return_value = 201
            s.trigger(None)

            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u1", "u2"], "upstreamBuilders": ["u1", "u2"], "lastCheck": 201, "lastReset": 201})
        d.addCallback(doTrigger)

        return d

    def testChangeConfiguration(self):
        # Test that when we change the set of upstreams/downstreams, our state
        # is adjusted. New builders should be added to remainingBuilders, and
        # upstreamBuilders should be set to the new value
        old_state = {"remainingBuilders": ["u1", "u3"], "upstreamBuilders": ["u1", "u2", "u3"], "lastCheck": 0}
        self.dbc.runQueryNow("""
                INSERT into schedulers
                (name, class_name, state) VALUES
                ('s1', 'buildbotcustom.scheduler.AggregatingScheduler', '%s')
        """ % self.dbc.quoteq(json.dumps(old_state)))

        s = AggregatingScheduler(name='s1', branch='b1', builderNames=['d1', 'd2'], upstreamBuilders=['u1', 'u4'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        d = self.dbc.addSchedulers([s])

        def start(_):
            s.startService()
            return s.run()
        d.addCallback(start)

        def check(_):
            requests = self.dbc.runQueryNow("SELECT * FROM buildrequests")
            self.assertEquals(len(requests), 0)
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"remainingBuilders": ["u1", "u4"], "upstreamBuilders": ["u1", "u4"], "lastCheck": 0, "lastReset": 0})
        d.addCallback(check)

        return d
