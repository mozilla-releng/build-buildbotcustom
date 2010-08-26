# Mozilla schedulers
# Based heavily on buildbot.scheduler
# Contributor(s):
#   Chris AtLee <catlee@mozilla.com>

from twisted.internet import defer
from twisted.python import log

from buildbot.scheduler import Scheduler
from buildbot.sourcestamp import SourceStamp

import time

class MultiScheduler(Scheduler):
    """Trigger N (default three) build requests based upon the same change request"""
    def __init__(self, numberOfBuildsToTrigger=3, **kwargs):
        self.numberOfBuildsToTrigger = numberOfBuildsToTrigger
        Scheduler.__init__(self, **kwargs)

    def _add_build_and_remove_changes(self, t, all_changes):
        db = self.parent.db
        for i in range(self.numberOfBuildsToTrigger):
            if self.treeStableTimer is None:
                # each Change gets a separate build
                for c in all_changes:
                    ss = SourceStamp(changes=[c])
                    ssid = db.get_sourcestampid(ss, t)
                    self.create_buildset(ssid, "scheduler", t)
            else:
                ss = SourceStamp(changes=all_changes)
                ssid = db.get_sourcestampid(ss, t)
                self.create_buildset(ssid, "scheduler", t)

        # and finally retire the changes from scheduler_changes
        changeids = [c.number for c in all_changes]
        db.scheduler_retire_changes(self.schedulerid, changeids, t)

class BuilderChooserScheduler(MultiScheduler):
    compare_attrs = MultiScheduler.compare_attrs + ('chooserFunc',)
    def __init__(self, chooserFunc, **kwargs):
        self.chooserFunc = chooserFunc
        MultiScheduler.__init__(self, **kwargs)

    def run(self):
        db = self.parent.db
        d = db.runInteraction(self.classify_changes)
        d.addCallback(lambda ign: db.runInteraction(self._process_changes))
        d.addCallback(self._maybeRunChooser)
        return d

    def _process_changes(self, t):
        db = self.parent.db
        res = db.scheduler_get_classified_changes(self.schedulerid, t)
        (important, unimportant) = res
        return self._checkTreeStableTimer(important, unimportant)

    def _checkTreeStableTimer(self, important, unimportant):
        """Look at the changes that need to be processed and decide whether
        to queue a BuildRequest or sleep until something changes.

        If I decide that a build should be performed, I will return the list of
        changes to be built.

        If the treeStableTimer has not elapsed, I will return the amount of
        time to wait before trying again.

        Otherwise I will return None.
        """

        if not important:
            # Don't do anything
            return None
        all_changes = important + unimportant
        most_recent = max([c.when for c in all_changes])
        if self.treeStableTimer is not None:
            now = time.time()
            stable_at = most_recent + self.treeStableTimer
            if stable_at > now:
                # Wake up one second late, to avoid waking up too early and
                # looping a lot.
                return stable_at + 1.0

        # ok, do a build for these changes
        return all_changes

    def _maybeRunChooser(self, res):
        if res is None:
            return None
        elif isinstance(res, (int, float)):
            return res
        else:
            assert isinstance(res, list)
            return self._runChooser(res)

    def _runChooser(self, all_changes):
        # Figure out which builders to run
        d = defer.maybeDeferred(self.chooserFunc, self, all_changes)

        def do_add_build_and_remove_changes(t, buildersPerChange):
            log.msg("Adding request for %s" % buildersPerChange)
            if not buildersPerChange:
                return

            db = self.parent.db
            for i in range(self.numberOfBuildsToTrigger):
                if self.treeStableTimer is None:
                    # each Change gets a separate build
                    for c in all_changes:
                        if c not in buildersPerChange:
                            continue
                        ss = SourceStamp(changes=[c])
                        ssid = db.get_sourcestampid(ss, t)
                        self.create_buildset(ssid, "scheduler", t, builderNames=buildersPerChange[c])
                else:
                    # Grab all builders
                    builderNames = set()
                    for names in buildersPerChange.values():
                        builderNames.update(names)
                    builderNames = list(builderNames)
                    ss = SourceStamp(changes=all_changes)
                    ssid = db.get_sourcestampid(ss, t)
                    self.create_buildset(ssid, "scheduler", t, builderNames=builderNames)

            # and finally retire the changes from scheduler_changes
            changeids = [c.number for c in all_changes]
            db.scheduler_retire_changes(self.schedulerid, changeids, t)
            return None

        d.addCallback(lambda buildersPerChange: self.parent.db.runInteraction(do_add_build_and_remove_changes, buildersPerChange))
        return d
