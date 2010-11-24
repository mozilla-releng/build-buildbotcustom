# Mozilla schedulers
# Based heavily on buildbot.scheduler
# Contributor(s):
#   Chris AtLee <catlee@mozilla.com>

from twisted.internet import defer
from twisted.python import log

from buildbot.scheduler import Scheduler
from buildbot.schedulers.base import BaseScheduler
from buildbot.schedulers.timed import Nightly
from buildbot.sourcestamp import SourceStamp
from buildbot.process.properties import Properties

from buildbot.util import now

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

class SpecificNightly(Nightly):
    """Subclass of regular Nightly scheduler that allows you to specify a function
    that gets called to generate a sourcestamp
    """
    def __init__(self, ssFunc, *args, **kwargs):
        self.ssFunc = ssFunc

        Nightly.__init__(self, *args, **kwargs)

    def start_HEAD_build(self, t):
        """Slightly mis-named, but this function is called when it's time to start a build.  We call our ssFunc to get a sourcestamp to build.

        ssFunc is called in a thread with an active database transaction running.
        """
        d = defer.maybeDeferred(self.ssFunc, self, t)

        def create_buildset(t, ss):
            # if our function returns None, don't create any build
            if ss is None:
                log.msg("%s: No sourcestamp returned from ssfunc; not scheduling a build" % self.name)
                return
            log.msg("%s: Creating buildset with sourcestamp %s" % (self.name, ss.getText()))
            db = self.parent.db
            ssid = db.get_sourcestampid(ss, t)
            self.create_buildset(ssid, self.reason, t)

        d.addCallback(lambda ss: self.parent.db.runInteraction(create_buildset, ss))

class PersistentScheduler(BaseScheduler):
    """Make sure at least numPending builds are pending on each of builderNames"""
    def __init__(self, numPending, pollInterval=60, ssFunc=None, properties={},
            **kwargs):
        self.numPending = numPending
        self.pollInterval = pollInterval
        self.lastCheck = 0
        if ssFunc is None:
            self.ssFunc = self._default_ssFunc
        else:
            self.ssFunc = ssFunc

        BaseScheduler.__init__(self, properties=properties, **kwargs)

    def _default_ssFunc(self, builderName):
        return SourceStamp()

    def run(self):
        if self.lastCheck + self.pollInterval > now():
            # Try again later
            return (self.lastCheck + self.pollInterval + 1)

        db = self.parent.db
        to_create = []
        for builderName in self.builderNames:
            n = len(db.get_pending_brids_for_builder(builderName))
            num_to_create = self.numPending - n
            if num_to_create <= 0:
                continue
            to_create.append( (builderName, num_to_create) )

        d = db.runInteraction(lambda t: self.create_builds(to_create, t))
        return d

    def create_builds(self, to_create, t):
        db = self.parent.db
        for builderName, count in to_create:
            ss = self.ssFunc(builderName)
            ssid = db.get_sourcestampid(ss, t)
            for i in range(0, count):
                self.create_buildset(ssid, "scheduler", t, builderNames=[builderName])

        # Try again in a bit
        self.lastCheck = now()
        return now() + self.pollInterval

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

def makePropertiesScheduler(base_class, propfuncs, *args, **kw):
    """Return a subclass of `base_class` that will call each of `propfuncs` to
    generate a set of properties to attach to new buildsets.

    Each function of propfuncs will be passed (scheduler instance, db
    transaction, sourcestamp id) and must return a Properties instance.  These
    properties will be added to any new buildsets this scheduler creates."""
    pf = propfuncs
    class S(base_class):
        compare_attrs = base_class.compare_attrs + ('propfuncs',)
        propfuncs = pf

        def create_buildset(self, ssid, reason, t, props=None, builderNames=None):
            # We need a fresh set of properties each time since we expect to update
            # the properties below
            my_props = Properties()
            if props is None:
                my_props.updateFromProperties(self.properties)
            else:
                my_props.updateFromProperties(props)

            # Update with our prop functions
            try:
                for func in propfuncs:
                    try:
                        request_props = func(self, t, ssid)
                        log.msg("%s: propfunc returned %s" % (self.name, request_props))
                        my_props.updateFromProperties(request_props)
                    except:
                        log.msg("Error running %s" % func)
                        log.err()
            except:
                log.msg("%s: error calculating properties" % self.name)
                log.err()

            # Call our base class's original, with our new properties.
            return base_class.create_buildset(self, ssid, reason, t, my_props, builderNames)

    # Copy the original class' name so that buildbot's ComparableMixin works
    S.__name__ = base_class.__name__ + "-props"

    return S
