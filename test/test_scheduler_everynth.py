# Tests for the EveryNthScheduler
import os
import shutil
import json

from buildbot.db import dbspec, connector
from buildbot.db.schema.manager import DBSchemaManager
from buildbot.changes.manager import ChangeManager
from buildbot.changes.changes import Change
from twisted.trial import unittest

import mock

from buildbotcustom.scheduler import EveryNthScheduler


class TestEveryNthScheduler(unittest.TestCase):
    basedir = "test_scheduler_everynth"

    def setUp(self):
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)
        os.makedirs(self.basedir)
        spec = dbspec.DBSpec.from_url("sqlite:///state.sqlite", self.basedir)
        manager = DBSchemaManager(spec, self.basedir)
        manager.upgrade()

        self.dbc = connector.DBConnector(spec)
        self.dbc.start()
        self.cm = ChangeManager()
        self.cm.parent = mock.Mock()
        self.cm.parent.db = self.dbc

        self._patcher = mock.patch("buildbotcustom.scheduler.now")
        self._time = self._patcher.start()
        self._time.return_value = 123

    def tearDown(self):
        self.dbc.stop()
        shutil.rmtree(self.basedir)
        self._patcher.stop()

    def testCreate(self):
        # Test the scheduler is created normally
        s = EveryNthScheduler(name='s1', n=2, branch='b1', builderNames=['d1', 'd2'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc
        s.parent.change_svc = self.cm

        d = self.dbc.addSchedulers([s])

        def checkState(_):
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"last_processed": 0})

        d.addCallback(checkState)
        return d

    def testTriggerDownstreams(self):
        # Test of the basic functionality. Do we fire our downstream builders
        # when we have enough changes?
        s = EveryNthScheduler(name='s1', branch='b1', builderNames=['d1'], n=2)
        s.parent = mock.Mock()
        s.parent.db = self.dbc
        s.parent.change_svc = self.cm

        d = self.dbc.addSchedulers([s])

        def check(_):
            requests = self.dbc.runQueryNow("SELECT * FROM buildrequests")
            self.assertEquals(len(requests), 0)
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"last_processed": 0})
        d.addCallback(check)

        # Add the first change
        def addChange1(_):
            c = Change(who='me!', branch='b1', revision='1', files=[],
                       comments='really important', revlink='from poller')
            self.dbc.addChangeToDatabase(c)
            # Now run the scheduler
            return s.run()
        d.addCallback(addChange1)

        # We shouldn't have any build requests created
        def checkRequests1(_):
            requests = self.dbc.runQueryNow("SELECT buildername FROM buildrequests WHERE complete=0")
            self.assertEquals(len(requests), 0)

            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            # Check that we did actually look at the change, but decided
            # against doing anything with it
            self.assertEquals(state, {"last_processed": 1})
        d.addCallback(checkRequests1)

        # Now add a 2nd change
        def addChange2(_):
            c = Change(who='me!', branch='b1', revision='2', files=[],
                       comments='really important', revlink='from poller')
            self.dbc.addChangeToDatabase(c)
            # Now run the scheduler
            return s.run()
        d.addCallback(addChange2)

        # We should have *two* buildrequests right now, one per important
        # change. These will normally be immediately coalesced into a single
        # job, but builder coalescing limits may impact that.
        def checkRequests2(_):
            requests = self.dbc.runQueryNow("SELECT buildername FROM buildrequests WHERE complete=0")
            self.assertEquals(len(requests), 2)
            self.assertEquals(sorted([r[0] for r in requests]), ['d1', 'd1'])
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"last_processed": 2})
        d.addCallback(checkRequests2)

        return d

    def testIdleTrigger(self):
        # Test of the idle timeout functionality. If we get n-1 changes, and
        # the timer fires, we should get builds.
        # when we have enough changes?
        s = EveryNthScheduler(name='s1', branch='b1', builderNames=['d1'], n=3, idleTimeout=30)
        s.parent = mock.Mock()
        s.parent.db = self.dbc
        s.parent.change_svc = self.cm

        d = self.dbc.addSchedulers([s])

        def check(_):
            requests = self.dbc.runQueryNow("SELECT * FROM buildrequests")
            self.assertEquals(len(requests), 0)
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"last_processed": 0})
        d.addCallback(check)

        # Add the first change
        def addChange(_):
            c = Change(who='me!', branch='b1', revision='1', files=[],
                       comments='really important', revlink='from poller', when=self._time())
            self.dbc.addChangeToDatabase(c)
            # Now run the scheduler
            return s.run()
        d.addCallback(addChange)

        # Advance time by 20s
        def addTime(_):
            self._time.return_value += 20
            return s.run()
        d.addCallback(addTime)

        # Add the second change
        d.addCallback(addChange)

        # Verify we have 0 buildrequests created
        def checkRequests1(_):
            requests = self.dbc.runQueryNow("SELECT buildername FROM buildrequests WHERE complete=0")
            self.assertEquals(len(requests), 0)

            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            # Check that we did actually look at the change, but decided
            # against doing anything with it
            self.assertEquals(state, {"last_processed": 2})
        d.addCallback(checkRequests1)

        # Advance time again, now we should have 2 requests
        d.addCallback(addTime)

        def checkRequests2(_):
            requests = self.dbc.runQueryNow("SELECT buildername FROM buildrequests WHERE complete=0")
            self.assertEquals(len(requests), 2)
            self.assertEquals(sorted([r[0] for r in requests]), ['d1', 'd1'])
            schedulers = self.dbc.runQueryNow("SELECT name, state FROM schedulers")
            self.assertEquals(len(schedulers), 1)
            state = json.loads(schedulers[0][1])
            self.assertEquals(state, {"last_processed": 2})
        d.addCallback(checkRequests2)

        return d
