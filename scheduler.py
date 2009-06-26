# Mozilla schedulers
# Based heavily on buildbot.scheduler
# Contributor(s):
#   Chris AtLee <catlee@mozilla.com>

import time

from twisted.internet import reactor
from twisted.python import log
from twisted.application import service

from buildbot.scheduler import Scheduler, Nightly
from buildbot import buildset, util
from buildbot.sourcestamp import SourceStamp
from buildbot.status.builder import SUCCESS, WARNINGS

class MozScheduler(Scheduler):
    """The MozScheduler class will run a build after some period of time
    called the C{treeStableTimer}, on a given set of Builders. It only pays
    attention to a single branch. You you can provide a C{fileIsImportant}
    function which will evaluate each Change to decide whether or not it
    should trigger a new build. If no builds have occurred after
    C{idleTimeout} seconds, then a build will be started. Set
    C{idleTimeout} to None to disable this.
    """

    fileIsImportant = None
    compare_attrs = ('name', 'treeStableTimer', 'builderNames', 'branch',
                     'fileIsImportant', 'properties', 'idleTimeout')
    
    def __init__(self, name, branch, treeStableTimer, builderNames,
                 idleTimeout=None, fileIsImportant=None, properties={}):
        """
        @param name: the name of this Scheduler
        @param branch: The branch name that the Scheduler should pay
                       attention to. Any Change that is not on this branch
                       will be ignored. It can be set to None to only pay
                       attention to the default branch.
        @param treeStableTimer: the duration, in seconds, for which the tree
                                must remain unchanged before a build will be
                                triggered. This is intended to avoid builds
                                of partially-committed fixes.
        @param idleTimeout: the duration, in seconds, for which no
                            builds have occurred before a build will
                            be triggered. This is intended to produce builds
                            with some minimum frequency even if the tree is not
                            changing.
        @param builderNames: a list of Builder names. When this Scheduler
                             decides to start a set of builds, they will be
                             run on the Builders named by this list.

        @param fileIsImportant: A callable which takes one argument (a Change
                                instance) and returns True if the change is
                                worth building, and False if it is not.
                                Unimportant Changes are accumulated until the
                                build is triggered by an important change.
                                The default value of None means that all
                                Changes are important.

        @param properties: properties to apply to all builds started from this 
                           scheduler
        """
        Scheduler.__init__(self, name, branch, treeStableTimer, builderNames,
                           fileIsImportant=fileIsImportant,
                           properties=properties)

        self.idleTimeout = idleTimeout
        self.idleTimer = None
        self.setIdleTimer()

    def setIdleTimer(self):
        if self.idleTimeout:
            if self.idleTimer:
                self.idleTimer.cancel()
            self.idleTimer = reactor.callLater(self.idleTimeout,
                    self.doIdleBuild)

    def stopTimer(self):
        Scheduler.stopTimer(self)
        if self.idleTimer:
            self.idleTimer.cancel()
            self.idleTimer = None

    def fireTimer(self):
        Scheduler.fireTimer(self)
        self.setIdleTimer()

    def doIdleBuild(self):
        self.idleTimer = None
        reason = ("The idle timer on '%s' triggered this build at %s"
                       % (self.name, time.strftime("%Y-%m-%d %H:%M:%S")))
        bs = buildset.BuildSet(self.builderNames,
                               SourceStamp(branch=self.branch),
                               reason,
                               properties=self.properties)
        self.submitBuildSet(bs)
        self.setIdleTimer()

class MultiScheduler(Scheduler):
    """Trigger N (default three) build requests based upon the same change request"""
    def __init__(self, numberOfBuildsToTrigger=3, **kwargs):
        self.numberOfBuildsToTrigger = numberOfBuildsToTrigger
        Scheduler.__init__(self, **kwargs)

    def fireTimer(self):
        self.timer = None
        self.nextBuildTime = None
        changes = self.importantChanges + self.unimportantChanges
        self.importantChanges = []
        self.unimportantChanges = []

        # submit
        for i in range(0, self.numberOfBuildsToTrigger):
            bs = buildset.BuildSet(self.builderNames, SourceStamp(changes=changes))
            self.submitBuildSet(bs)

class NoMergeSourceStamp(SourceStamp):
    def canBeMergedWith(self, other):
        return False

class NoMergeScheduler(Scheduler):
    """Disallow build requests to be merged"""
    def fireTimer(self):
        self.timer = None
        self.nextBuildTime = None

        for change in self.importantChanges:
            changes = [change]
            if self.unimportantChanges:
                changes.extend(self.unimportantChanges)
                self.unimportantChanges = []

            # submit
            ss = NoMergeSourceStamp(changes=changes)
            bs = buildset.BuildSet(self.builderNames, ss)
            self.submitBuildSet(bs)
        self.importantChanges = []

class NoMergeMultiScheduler(Scheduler):
    """Disallow build requests to be merged"""
    def __init__(self, numberOfBuildsToTrigger=1, **kwargs):
        self.numberOfBuildsToTrigger = numberOfBuildsToTrigger
        Scheduler.__init__(self, **kwargs)

    def fireTimer(self):
        self.timer = None
        self.nextBuildTime = None

        for change in self.importantChanges:
            changes = [change]
            if self.unimportantChanges:
                changes.extend(self.unimportantChanges)
                self.unimportantChanges = []

            # submit
            for i in range(self.numberOfBuildsToTrigger):
                ss = NoMergeSourceStamp(changes=changes)
                bs = buildset.BuildSet(self.builderNames, ss)
                self.submitBuildSet(bs)
        self.importantChanges = []

class NightlyRebuild(Nightly):
    """The NightlyRebuild scheduler will rebuild previously run builds on a certain schedule.
    It accepts the same parameters as the C{Nightly} scheduler, as well as
    C{numberOfBuildsToTrigger}, C{mergeBuilds}, and C{statusList} parameters.

    The scheduler will schedule C{numberOfBuildsToTrigger} builds on the
    specified schedule.  If C{mergeBuilds} is set to False, then the rebuild
    requests will not be merged together.  C{statusList}, if set, should be a
    list of build statuses that are used to determine which past build to
    rebuild.  If not set it defaults to [SUCCESS, WARNINGS] which means that
    the previous build that completed with status of either SUCCESS or WARNINGS
    will be rebuilt."""

    compare_attrs = Nightly.compare_attrs + ('numberOfBuildsToTrigger', 'mergeBuilds', 'statusList')

    def __init__(self, numberOfBuildsToTrigger=1, mergeBuilds=False, statusList=None, **kwargs):
        self.numberOfBuildsToTrigger = numberOfBuildsToTrigger
        self.mergeBuilds = mergeBuilds
        self.statusList = statusList or [SUCCESS, WARNINGS]
        Nightly.__init__(self, **kwargs)

    def doPeriodicBuild(self):
        # Schedule the next run
        self.setTimer()

        # For each of our builders, find the previous build that completed with
        # one of the desired status results and then re-run it.
        for builderName in self.builderNames:
            builder = self.parent.status.getBuilder(builderName)
            lastBuild = builder.getLastFinishedBuild()
            while lastBuild is not None:
                if lastBuild.isFinished() and lastBuild.getResults() in self.statusList:
                    break
                else:
                    try:
                        lastBuild = builder.getBuildByNumber(lastBuild.getNumber() - 1)
                    except IndexError:
                        lastBuild = None

            if not lastBuild:
                continue

            for i in range(self.numberOfBuildsToTrigger):
                reason = self.reason + "; rebuilding build %i" % lastBuild.getNumber()
                if self.numberOfBuildsToTrigger > 1:
                    reason += ", %i/%i" % (i+1, self.numberOfBuildsToTrigger)
                ss = lastBuild.getSourceStamp(absolute=True)
                if not self.mergeBuilds:
                    ss = NoMergeSourceStamp(changes=ss.changes,
                                            revision=ss.revision,
                                            branch=ss.branch,
                                            patch=ss.patch)
                bs = buildset.BuildSet([builderName],
                                       ss,
                                       reason,
                                       properties=self.properties)
                self.submitBuildSet(bs)
