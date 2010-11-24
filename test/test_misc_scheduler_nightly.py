import os, shutil
from twisted.trial import unittest

from buildbot.schedulers.basic import Scheduler
from buildbot.db import dbspec, connector
from buildbot.db.schema.manager import DBSchemaManager
from buildbot.changes.changes import Change

from buildbotcustom.misc_scheduler import lastChangeset, lastGoodRev, \
    getLatestRev, getLastBuiltRevision, lastGoodFunc
from buildbotcustom.scheduler import SpecificNightly

import mock

def createTestData(db):
    # Set up our test data
    # Sourcestamps
    db.runQueryNow("""INSERT INTO sourcestamps (`id`, `branch`, `revision`) VALUES (1, 'b1', 'r1')""")
    db.runQueryNow("""INSERT INTO sourcestamps (`id`, `branch`, `revision`) VALUES (2, 'b2', 'r2')""")

    # Change
    db.runQueryNow("""INSERT INTO changes
                    (`changeid`, `author`, `is_dir`, `comments`, `when_timestamp`, `branch`, `revision`)
                    VALUES
                    (1, 'me', 0, 'here', 1, 'b1', 'r1')""")
    db.runQueryNow("""INSERT INTO changes
                    (`changeid`, `author`, `is_dir`, `comments`, `when_timestamp`, `branch`, `revision`)
                    VALUES
                    (2, 'me', 0, 'here', 1, 'b2', 'r234567890')""")

    # Buildsets
    for i in range(1,3):
        db.runQueryNow("""INSERT INTO buildsets (`id`, `sourcestampid`, `submitted_at`) VALUES (%(i)i, %(i)i, 1)""" % dict(i=i))

    # Build requests
    # Two builders on branch1 that succeed
    db.runQueryNow("""INSERT INTO buildrequests (`buildsetid`, `complete`, `results`, `buildername`, `complete_at`, `submitted_at`) VALUES (1, 1, 0, 'builder1', 2, 1)""")
    db.runQueryNow("""INSERT INTO buildrequests (`buildsetid`, `complete`, `results`, `buildername`, `complete_at`, `submitted_at`) VALUES (1, 1, 0, 'builder2', 2, 1)""")

    # Two builders on branch2, one succeeds, one fails
    db.runQueryNow("""INSERT INTO buildrequests (`buildsetid`, `complete`, `results`, `buildername`, `complete_at`, `submitted_at`) VALUES (2, 1, 0, 'builder1', 2, 1)""")
    db.runQueryNow("""INSERT INTO buildrequests (`buildsetid`, `complete`, `results`, `buildername`, `complete_at`, `submitted_at`) VALUES (2, 1, 2, 'builder2', 2, 1)""")


class TestLastGoodFuncs(unittest.TestCase):
    basedir = "test_misc_scheduler_nightly"
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

    def test_lastChangeset(self):
        # First, we need to add a few changes!
        c1 = Change(who='me!', branch='b1', revision='1', files=[], comments='really important')
        c2 = Change(who='me!', branch='b2', revision='2', files=[], comments='really important')
        c3 = Change(who='me!', branch='b1', revision='3', files=[], comments='really important')
        for c in [c1, c2, c3]:
            self.dbc.addChangeToDatabase(c)

        c = lastChangeset(self.dbc, 'b1')
        self.assertEquals(c, c3.revision)

        c = lastChangeset(self.dbc, 'b2')
        self.assertEquals(c, c2.revision)

    def test_lastGoodRev(self):
        createTestData(self.dbc)

        # Check that we can find the good revision for builder1, builder2
        rev = self.dbc.runInteractionNow(lambda t: lastGoodRev(self.dbc, t, 'b1', ['builder1', 'builder2'], 0, 3))
        self.assertEquals(rev, 'r1')

        # Check that we can't find a good revision after the last builds on builder1, builder2 are done.
        rev = self.dbc.runInteractionNow(lambda t: lastGoodRev(self.dbc, t, 'b1', ['builder1', 'builder2'], 4, 6))
        self.assertEquals(rev, None)

        # Check that we can't find a good revision on branch b2
        rev = self.dbc.runInteractionNow(lambda t: lastGoodRev(self.dbc, t, 'b2', ['builder1', 'builder2'], 0, 3))
        self.assertEquals(rev, None)

    def test_getLatestRev(self):
        # First, we need to add a few changes!
        c1 = Change(who='me!', branch='b1', revision='1', files=[], comments='really important', when=1)
        c2 = Change(who='me!', branch='b2', revision='2', files=[], comments='really important', when=2)
        c3 = Change(who='me!', branch='b1', revision='3', files=[], comments='really important', when=3)
        for c in [c1, c2, c3]:
            self.dbc.addChangeToDatabase(c)

        rev = self.dbc.runInteractionNow(lambda t: getLatestRev(self.dbc, t, 'b1', '1', '3'))
        self.assertEquals(rev, '3')

        # Revision 2 isn't on branch b1, so revision 1 should be latest
        rev = self.dbc.runInteractionNow(lambda t: getLatestRev(self.dbc, t, 'b1', '1', '2'))
        self.assertEquals(rev, '1')

        # Revision 1 and 1 are the same
        rev = self.dbc.runInteractionNow(lambda t: getLatestRev(self.dbc, t, 'b1', '1', '1'))
        self.assertEquals(rev, '1')

    def test_getLastBuiltRevision(self):
        createTestData(self.dbc)

        # r1 is the latest on branch b1
        rev = self.dbc.runInteractionNow(lambda t: getLastBuiltRevision(self.dbc, t, 'b1', ['builder1', 'builder2']))
        self.assertEquals(rev, 'r1')

        # We do LIKE matching on changes.revision so we can get the full revisions.
        rev = self.dbc.runInteractionNow(lambda t: getLastBuiltRevision(self.dbc, t, 'b2', ['builder1', 'builder2']))
        self.assertEquals(rev, 'r234567890')

        # Nothing has happened on branch b3
        rev = self.dbc.runInteractionNow(lambda t: getLastBuiltRevision(self.dbc, t, 'b3', ['builder1', 'builder2']))
        self.assertEquals(rev, None)

    def test_lastGoodFunc(self):
        createTestData(self.dbc)

        ssFunc = lastGoodFunc('b1', ['builder1', 'builder2'])
        ss = self.dbc.runInteractionNow(lambda t: ssFunc(self.s, t))
        self.assertEquals(ss.revision, 'r1')

        ssFunc = lastGoodFunc('b2', ['builder1', 'builder2'])
        ss = self.dbc.runInteractionNow(lambda t: ssFunc(self.s, t))
        self.assertEquals(ss.revision, 'r234567890')

class TestSpecificNightlyScheduler(unittest.TestCase):
    basedir = "test_misc_scheduler_nightly_scheduler"
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

    def testSpecificNightlyScheduler(self):
        createTestData(self.dbc)
        ssFunc = lastGoodFunc('b2', ['builder1', 'builder2'])
        s = SpecificNightly(ssFunc, name='s', builderNames=['nightly1'])
        s.parent = mock.Mock()
        s.parent.db = self.dbc

        d = self.dbc.addSchedulers([s])

        def startBuild(ign):
            return self.dbc.runInteractionNow(lambda t: s.start_HEAD_build(t))

        d.addCallback(startBuild)

        def check(ign):
            # Check that we have a buildrequest for revision r1
            req = self.dbc.runQueryNow("""
                SELECT * FROM buildrequests, buildsets, sourcestamps
                WHERE
                    buildrequests.buildsetid = buildsets.id AND
                    buildsets.sourcestampid = sourcestamps.id AND
                    buildername='nightly1' AND
                    revision = 'r1'
                    """)
            self.assertEquals(len(req), 1)

        return d
