from __future__ import with_statement
import os, shutil
from twisted.trial import unittest

from buildbot.schedulers.basic import Scheduler
from buildbot.db import dbspec, connector
from buildbot.db.schema.manager import DBSchemaManager
from buildbot.changes.changes import Change

import mock

from buildbotcustom.misc_scheduler import buildIDSchedFunc, buildUIDSchedFunc

class TestPropFuncs(unittest.TestCase):
    basedir = "test_misc_scheduler_propfuncs"
    def setUp(self):
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)
        os.makedirs(self.basedir)
        spec = dbspec.DBSpec.from_url("sqlite:///state.sqlite", self.basedir)
        manager = DBSchemaManager(spec, self.basedir)
        manager.upgrade()

        self.dbc = connector.DBConnector(spec)
        self.dbc.start()

        self.s = Scheduler(name="s", builderNames=["b1"])
        self.s.parent = mock.Mock()
        self.s.parent.db = self.dbc

        return self.dbc.addSchedulers([self.s])

    def tearDown(self):
        self.dbc.stop()
        shutil.rmtree(self.basedir)

    def test_buildIDSchedFunc(self):
        import time
        with mock.patch.object(time, 'time') as time_method:
            time_method.return_value = 10

            self.dbc.runInteractionNow(lambda t: buildIDSchedFunc(self.s, t, None))
            state = self.dbc.runInteractionNow(lambda t: self.s.get_state(t))
            self.assertEquals(state['last_buildid'], time.strftime("%Y%m%d%H%M%S", time.localtime(10)))

            # Running this again at the same time should increment our buildid by 1
            self.dbc.runInteractionNow(lambda t: buildIDSchedFunc(self.s, t, None))
            state = self.dbc.runInteractionNow(lambda t: self.s.get_state(t))
            self.assertEquals(state['last_buildid'], time.strftime("%Y%m%d%H%M%S", time.localtime(11)))

            # If time happens to go backwards, our buildid shouldn't
            time_method.return_value = 8
            self.dbc.runInteractionNow(lambda t: buildIDSchedFunc(self.s, t, None))
            state = self.dbc.runInteractionNow(lambda t: self.s.get_state(t))
            self.assertEquals(state['last_buildid'], time.strftime("%Y%m%d%H%M%S", time.localtime(12)))

    def test_buildUIDSchedFunc(self):
        import uuid
        with mock.patch.object(uuid, 'uuid4') as uuid4_method:
            uuid4_method.return_value.hex = '1234567890abcdef'
            props = self.dbc.runInteractionNow(lambda t: buildUIDSchedFunc(self.s, t, None))
            self.assertEquals(props['builduid'], '1234567890abcdef')
