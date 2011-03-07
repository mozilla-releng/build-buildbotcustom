import os, shutil

from twisted.trial import unittest
from twisted.python import log
from twisted.internet import defer

from buildbot.schedulers.basic import Scheduler
from buildbot.db import dbspec, connector
from buildbot.db.schema.manager import DBSchemaManager
from buildbot.changes.changes import Change
from buildbot.process.builder import Builder
from buildbot.changes.manager import ChangeManager
from buildbot.process.factory import BuildFactory
from buildbot.steps.dummy import Dummy
from buildbot.sourcestamp import SourceStamp
from buildbot.process.properties import Properties

from buildbotcustom.process.factory import RequestSortingBuildFactory

import mock

class TestTestOrder(unittest.TestCase):
    """Tests that tests are run according to their corresponding build's start
    time"""

    basedir = "test_test_order"

    def setUp(self):
        if os.path.exists(self.basedir):
            shutil.rmtree(self.basedir)
        os.makedirs(self.basedir)
        spec = dbspec.DBSpec.from_url("sqlite:///state.sqlite", self.basedir)
        manager = DBSchemaManager(spec, self.basedir)
        manager.upgrade()

        self.dbc = connector.DBConnector(spec)
        self.dbc.start()

        parent = mock.Mock()
        parent.db = self.dbc

        change_manager = ChangeManager()
        change_manager.parent = parent

        parent.change_svc = change_manager

        self.s = Scheduler(name="s", builderNames=["b1"])
        self.s.parent = parent


        return self.dbc.addSchedulers([self.s])

    def makeBuilder(self, klass):
        dummy_factory = klass([Dummy()])
        builder = {
                'name': 'b1',
                'slavenames': ['s1'],
                'builddir': 'b1',
                'slavebuilddir': 'b1',
                'factory': dummy_factory,
                }
        builder_status = mock.Mock()

        builder = Builder(builder, builder_status)
        builder.running = True
        builder.db = self.dbc
        builder.master_name = 'test_master'
        builder.master_incarnation = '12345'
        builder.botmaster = mock.Mock()
        builder.botmaster.shouldMergeRequests.return_value = True
        return builder

    def tearDown(self):
        self.dbc.stop()
        shutil.rmtree(self.basedir)

    def test_singleRequest(self):
        # Easy peasy
        c1 = Change(who='me!', branch='b1', revision='1', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000001'})
        self.dbc.addChangeToDatabase(c1)

        builder = self.makeBuilder(BuildFactory)

        # Run the scheduler
        d = self.s.run()

        # Check that we have a build request
        def checkRequests(ign):
            req = self.dbc.runQueryNow("select count(*) from buildrequests")
            self.assertEquals(req[0][0], 1)
        d.addCallback(checkRequests)

        # Claim the request
        def claimRequest(ign):
            d = self.dbc.runInteraction(builder._claim_buildreqs, ["slave!"])
            return d
        d.addCallback(claimRequest)

        # Start the build!
        def startBuild(assignments):
            self.assertEquals(len(assignments.keys()), 1)
            requests = assignments.values()[0]
            self.assertEquals(len(requests), 1)

            build = builder.buildFactory.newBuild(requests)
            self.assertEquals(build.source.revision, '1')

            changes = build.source.changes
            self.assertEquals(changes[0].revision, '1')
            return
        d.addCallback(startBuild)

        return d

    def test_threeRequests(self):
        # Three changes, r1, r2, r3
        # They start in order, and finish in reverse order
        # We want the to be testing r3 in the end
        c1 = Change(who='me!', branch='b1', revision='r3', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000003'}, when=4)
        c2 = Change(who='me!', branch='b1', revision='r2', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000002'}, when=5)
        c3 = Change(who='me!', branch='b1', revision='r1', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000001'}, when=6)
        self.dbc.addChangeToDatabase(c1)
        self.dbc.addChangeToDatabase(c2)
        self.dbc.addChangeToDatabase(c3)

        # Run the scheduler
        d = self.s.run()

        builder = self.makeBuilder(RequestSortingBuildFactory)

        # Check that we have three build requests
        def checkRequests(ign):
            req = self.dbc.runQueryNow("select count(*) from buildrequests")
            self.assertEquals(req[0][0], 3)
        d.addCallback(checkRequests)

        # Claim the request
        def claimRequest(ign):
            d = self.dbc.runInteraction(builder._claim_buildreqs, ["slave!"])
            return d
        d.addCallback(claimRequest)

        # Start the build!
        def startBuild(assignments):
            self.assertEquals(len(assignments.keys()), 1)
            requests = assignments.values()[0]
            self.assertEquals(len(requests), 3)

            build = builder.buildFactory.newBuild(requests)
            self.assertEquals(build.source.revision, 'r3')

            changes = build.source.changes
            # The last revision should be r3, since it started latest
            self.assertEquals(changes[0].revision, 'r1')
            self.assertEquals(changes[1].revision, 'r2')
            self.assertEquals(changes[2].revision, 'r3')
            return
        d.addCallback(startBuild)
        return d

    def test_threeRequestsUnsorted(self):
        # Three changes, r1, r2, r3
        # They start in order, and finish in reverse order
        # We want the to be testing r3 in the end
        c1 = Change(who='me!', branch='b1', revision='r3', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000003'}, when=4)
        c2 = Change(who='me!', branch='b1', revision='r2', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000002'}, when=5)
        c3 = Change(who='me!', branch='b1', revision='r1', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000001'}, when=6)
        self.dbc.addChangeToDatabase(c1)
        self.dbc.addChangeToDatabase(c2)
        self.dbc.addChangeToDatabase(c3)

        # Run the scheduler
        d = self.s.run()

        builder = self.makeBuilder(BuildFactory)

        # Check that we have three build requests
        def checkRequests(ign):
            req = self.dbc.runQueryNow("select count(*) from buildrequests")
            self.assertEquals(req[0][0], 3)
        d.addCallback(checkRequests)

        # Claim the request
        def claimRequest(ign):
            d = self.dbc.runInteraction(builder._claim_buildreqs, ["slave!"])
            return d
        d.addCallback(claimRequest)

        # Start the build!
        def startBuild(assignments):
            self.assertEquals(len(assignments.keys()), 1)
            requests = assignments.values()[0]
            self.assertEquals(len(requests), 3)

            build = builder.buildFactory.newBuild(requests)
            self.assertEquals(build.source.revision, 'r1')

            changes = build.source.changes
            # The last revision should be r1, since it was submitted latest
            self.assertEquals(changes[0].revision, 'r3')
            self.assertEquals(changes[1].revision, 'r2')
            self.assertEquals(changes[2].revision, 'r1')
            return
        d.addCallback(startBuild)
        return d

    def test_threeRequestsManual(self):
        # Three changes, r1, r2, r3
        # r2, and r3 have buildids
        # r1 is manually triggered, and is submitted later
        # we want to be testing r1 in the end
        c1 = Change(who='me!', branch='b1', revision='r3', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000003'}, when=4)
        c2 = Change(who='me!', branch='b1', revision='r2', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000002'}, when=5)
        c3 = Change(who='me!', branch='b1', revision='r1', files=['http://path/to/build'],
                comments='really important', when=6)
        self.dbc.addChangeToDatabase(c1)
        self.dbc.addChangeToDatabase(c2)
        self.dbc.addChangeToDatabase(c3)

        # Run the scheduler
        d = self.s.run()

        builder = self.makeBuilder(RequestSortingBuildFactory)

        # Check that we have three build requests
        def checkRequests(ign):
            req = self.dbc.runQueryNow("select count(*) from buildrequests")
            self.assertEquals(req[0][0], 3)
        d.addCallback(checkRequests)

        # Claim the request
        def claimRequest(ign):
            d = self.dbc.runInteraction(builder._claim_buildreqs, ["slave!"])
            return d
        d.addCallback(claimRequest)

        # Start the build!
        def startBuild(assignments):
            self.assertEquals(len(assignments.keys()), 1)
            requests = assignments.values()[0]
            self.assertEquals(len(requests), 3)

            build = builder.buildFactory.newBuild(requests)
            self.assertEquals(build.source.revision, 'r1')

            changes = build.source.changes
            # The last revision should be r1, since it was manually triggered later
            self.assertEquals(changes[0].revision, 'r2')
            self.assertEquals(changes[1].revision, 'r3')
            self.assertEquals(changes[2].revision, 'r1')
            return
        d.addCallback(startBuild)
        return d

    def test_Rebuilds(self):
        # Two changes, r2, r3
        # They start in order, and finish in reverse order
        # We also have a pending request to re-build r1 that happens after r2, r3 have started
        # We want to end up re-building r1
        c1 = Change(who='me!', branch='b1', revision='r3', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000003'}, when=4)
        c2 = Change(who='me!', branch='b1', revision='r2', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000002'}, when=5)
        self.dbc.addChangeToDatabase(c1)
        self.dbc.addChangeToDatabase(c2)

        # Run the scheduler
        d = self.s.run()

        # Rebuild r1
        def addRebuild(t):
            c3 = Change(who='me!', branch='b1', revision='r1', files=['http://path/to/build'],
                    comments='really important', properties={'buildid': '20110214000001'}, when=6)
            self.dbc.addChangeToDatabase(c3)
            ss = SourceStamp(branch='b1', changes=[c3], revision='r1')
            ss1 = ss.getAbsoluteSourceStamp('r1')
            ssid = self.dbc.get_sourcestampid(ss, t)
            bsid = self.dbc.create_buildset(ssid, "rebuild", Properties(), ["b1"], t)

        d.addCallback(lambda ign: self.dbc.runInteractionNow(addRebuild))

        builder = self.makeBuilder(RequestSortingBuildFactory)

        # Check that we have three build requests
        def checkRequests(ign):
            req = self.dbc.runQueryNow("select count(*) from buildrequests")
            self.assertEquals(req[0][0], 3)
        d.addCallback(checkRequests)

        # Claim the request
        def claimRequest(ign):
            d = self.dbc.runInteraction(builder._claim_buildreqs, ["slave!"])
            return d
        d.addCallback(claimRequest)

        # Start the build!
        def startBuild(assignments):
            self.assertEquals(len(assignments.keys()), 1)
            requests = assignments.values()[0]
            self.assertEquals(len(requests), 3)

            build = builder.buildFactory.newBuild(requests)
            self.assertEquals(build.source.revision, 'r1')

            changes = build.source.changes
            # The last revision should be r1, since it was manually triggered later
            self.assertEquals(changes[0].revision, 'r2')
            self.assertEquals(changes[1].revision, 'r3')
            self.assertEquals(changes[2].revision, 'r1')
            return
        d.addCallback(startBuild)
        return d

    def test_brokenRequest(self):
        c1 = Change(who='me!', branch='b1', revision='1', files=['http://path/to/build'],
                comments='really important', properties={'buildid': '20110214000001'})
        self.dbc.addChangeToDatabase(c1)

        builder = self.makeBuilder(RequestSortingBuildFactory)

        # Run the scheduler
        d = self.s.run()

        # Check that we have a build request
        def checkRequests(ign):
            req = self.dbc.runQueryNow("select count(*) from buildrequests")
            self.assertEquals(req[0][0], 1)
        d.addCallback(checkRequests)

        # Claim the request
        def claimRequest(ign):
            d = self.dbc.runInteraction(builder._claim_buildreqs, ["slave!"])
            return d
        d.addCallback(claimRequest)

        # Start the build!
        def startBuild(assignments):
            self.assertEquals(len(assignments.keys()), 1)
            requests = assignments.values()[0]
            # Hack the request to break the sorting
            requests[0].submittedAt = "asdf"
            requests[0].reason = "rebuild"

            self.assertEquals(len(requests), 1)

            build = builder.buildFactory.newBuild(requests)
            # This should have generated an error
            self.failUnless(len(self.flushLoggedErrors()) == 1)
            self.assertEquals(build.source.revision, '1')

            changes = build.source.changes
            self.assertEquals(changes[0].revision, '1')
            return
        d.addCallback(startBuild)

        return d
