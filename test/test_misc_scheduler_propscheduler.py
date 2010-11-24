import os, shutil

from twisted.trial import unittest

from buildbot.db import dbspec, connector
from buildbot.db.schema.manager import DBSchemaManager
from buildbot.schedulers.basic import Scheduler
from buildbot.changes.changes import Change

from buildbotcustom.scheduler import makePropertiesScheduler
from buildbotcustom.misc_scheduler import buildIDSchedFunc, buildUIDSchedFunc

import mock

class TestPropertiesScheduler(unittest.TestCase):
    basedir = "test_misc_scheduler_propscheduler"
    def setUp(self):
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)
        os.makedirs(self.basedir)
        spec = dbspec.DBSpec.from_url("sqlite:///state.sqlite", self.basedir)
        manager = DBSchemaManager(spec, self.basedir)
        manager.upgrade()

        self.dbc = connector.DBConnector(spec)
        self.dbc.start()

    def tearDown(self):
        self.dbc.stop()
        shutil.rmtree(self.basedir)

    def testPropsScheduler(self):
        S = makePropertiesScheduler(Scheduler, propfuncs=[buildIDSchedFunc, buildUIDSchedFunc])
        s = S(name="s", builderNames=["b1"])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        c1 = Change(who='me!', branch='b1', revision='1', files=[], comments='really important')
        self.dbc.addChangeToDatabase(c1)
        s.parent.change_svc.getChangesGreaterThan.return_value = [c1]

        d = self.dbc.addSchedulers([s])

        d.addCallback(lambda ign: s.run())

        def check(ign):
            requests = self.dbc.runQueryNow("select * from buildrequests")
            self.assertEquals(len(requests), 1)
            props = self.dbc.runQueryNow("select * from buildset_properties")
            self.assertEquals(len(props), 3)
            # Make sure we haven't modifed the scheduler's internal set of properties
            self.assertEquals(s.properties.asList(), [("scheduler", "s", "Scheduler")])
        d.addCallback(check)
        return d
