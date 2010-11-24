# Additional Scheduler functions
# Contributor(s):
#   Chris AtLee <catlee@mozilla.com>
#   Lukas Blakk <lsblakk@mozilla.com>
import re, time
from twisted.python import log
from twisted.internet import defer
from twisted.web.client import getPage

from buildbot.sourcestamp import SourceStamp

import buildbotcustom.try_parser
reload(buildbotcustom.try_parser)

from buildbotcustom.try_parser import TryParser
from buildbotcustom.common import genBuildID, genBuildUID

from buildbot.process.properties import Properties
from buildbot.util import json

def tryChooser(s, all_changes):
    log.msg("Looking at changes: %s" % all_changes)

    buildersPerChange = {}

    dl = []

    def getJSON(data):
        push = json.loads(data)
        log.msg("Looking at the push json data for try comments")
        for p in push:
            pd = push[p]
            changes = pd['changesets']
            for change in reversed(changes):
                match = re.search("try:", change['desc'])
                if match:
                    return change['desc'].encode("utf8", "replace")

    def parseData(comments, c):
        if not comments:
            # still need to parse a comment string to get the default set
            log.msg("No comments, passing empty string which will result in default set")
            comments = ""
        customBuilders = TryParser(comments, s.builderNames)
        buildersPerChange[c] = customBuilders

    def parseDataError(failure, c):
        log.msg("Couldn't parse data: Requesting default try set.")
        parseData("", c)

    for c in all_changes:
      try:
        match = re.search("try", c.branch)
        if not match:
            log.msg("Ignoring off-branch %s" % c.branch)
            continue
        # Look in comments first for try: syntax
        match = re.search("try:", c.comments)
        if match:
            log.msg("Found try message in the change comments, ignoring push comments")
            d = defer.succeed(c.comments)
        # otherwise getPage from hg.m.o
        else:
            d = getPage(str("http://hg.mozilla.org/try/json-pushes?full=1&changeset=%s" % c.revision))
            d.addCallback(getJSON)
      except:
        log.msg("Error in all_changes loop: sending default try set")
        d = defer.succeed("")
      d.addCallback(parseData, c)
      d.addErrback(parseDataError, c)
      dl.append(d)
    d = defer.DeferredList(dl)
    d.addCallback(lambda res: buildersPerChange)
    return d

def buildIDSchedFunc(sched, t, ssid):
    """Generates a unique buildid for this change.

    Returns a Properties instance with 'buildid' set to the buildid to use.

    scheduler `sched`'s state is modified as a result."""
    state = sched.get_state(t)

    # Get the last buildid we scheduled from the database
    lastid = state.get('last_buildid', 0)

    newid = genBuildID()

    # Our new buildid will be the highest of the last buildid+1 or the buildid
    # based on the current date
    newid = str(max(int(newid), int(lastid)+1))

    # Save it in the scheduler's state so we don't generate the same one again.
    state['last_buildid'] = newid
    sched.set_state(t, state)

    props = Properties()
    props.setProperty('buildid', newid, 'buildIDSchedFunc')
    return props

def buildUIDSchedFunc(sched, t, ssid):
    """Return a Properties instance with 'builduid' set to a randomly generated
    id."""
    props = Properties()
    props.setProperty('builduid', genBuildUID(), 'buildUIDSchedFunc')
    return props

def lastChangeset(db, branch):
    """Returns the revision for the last changeset on the given branch"""
    for c in db.changeEventGenerator(branches=[branch]):
        return c.revision
    return None

def lastGoodRev(db, t, branch, builderNames, starttime, endtime):
    """Returns the revision for the latest green build among builders.  If no
    revision is all green, None is returned."""

    # Get a list of branch, revision, buildername tuples from builds on
    # `branch` that completed successfully or with warnings within [starttime,
    # endtime] (a closed interval) 
    q = db.quoteq("""SELECT branch, revision, buildername FROM
                sourcestamps,
                buildsets,
                buildrequests

            WHERE
                buildsets.sourcestampid = sourcestamps.id AND
                buildrequests.buildsetid = buildsets.id AND
                buildrequests.complete = 1 AND
                buildrequests.results IN (0,1) AND
                sourcestamps.revision IS NOT NULL AND
                buildrequests.buildername in %s AND
                sourcestamps.branch = ? AND
                buildrequests.complete_at >= ? AND
                buildrequests.complete_at <= ?

            ORDER BY
                buildsets.id DESC
        """ % db.parmlist(len(builderNames)))
    t.execute(q, tuple(builderNames) + (branch, starttime, endtime))
    builds = t.fetchall()

    builderNames = set(builderNames)

    # Map of (branch, revision) to set of builders that passed
    good_sourcestamps = {}

    # Go through the results and group them by branch,revision.
    # When we find a revision where all our required builders are listed, we've
    # found a good revision!
    count = 0
    for (branch, revision, name) in builds:
        count += 1
        key = branch, revision
        good_sourcestamps.setdefault(key, set()).add(name)

        if good_sourcestamps[key] == builderNames:
            # Looks like a winner!
            log.msg("lastGood: ss %s good for everyone!" % (key,))
            log.msg("lastGood: looked at %i builds" % count)
            return revision
    return None

def getLatestRev(db, t, branch, r1, r2):
    """Returns whichever of r1, r2 has the latest when_timestamp"""
    if r1 == r2:
        return r1

    # Get the when_timestamp for these two revisions
    q = db.quoteq("""SELECT revision FROM changes
                     WHERE
                        branch = ? AND
                        revision IN (?, ?)
                     ORDER BY
                        when_timestamp DESC
                     LIMIT 1""")
    t.execute(q, (branch, r1, r2))
    return t.fetchone()[0]

def getLastBuiltRevision(db, t, branch, builderNames):
    """Returns the latest revision that was built on builderNames"""
    # Utility function to handle concatenation differently depending on what
    # database we're talking to. mysql uses the CONCAT() function whereas
    # sqlite uses the || operator.
    def concat(a, b):
        if 'sqlite' in db._spec.dbapiName:
            return "%s || %s" % (a, b)
        else:
            # Make sure any % are escaped since mysql uses % as the parameter
            # style.
            return "CONCAT(%s, %s)" % (a.replace("%", "%%"), b.replace("%", "%%"))

    # Find the latest revision we built on any one of builderNames.
    # We match on changes.revision LIKE sourcestamps.revision + "%" in order to
    # handle forced builds where only the short revision has been specified.
    # This revision will show up as the sourcestamp's revision.
    q = db.quoteq("""SELECT changes.revision FROM
                buildrequests, buildsets, sourcestamps, changes
            WHERE
                buildrequests.buildsetid = buildsets.id AND
                buildsets.sourcestampid = sourcestamps.id AND
                changes.revision LIKE %s AND
                changes.branch = ? AND
                buildrequests.buildername IN %s
            ORDER BY
                changes.when_timestamp DESC
            LIMIT 1""" % (
                concat('sourcestamps.revision', "'%'"),
                db.parmlist(len(builderNames)))
            )

    t.execute(q, (branch,) + tuple(builderNames))
    result = t.fetchone()
    if result:
        return result[0]
    return None

def lastGoodFunc(branch, builderNames):
    """Returns a function that returns the latest revision on branch that was
    green for all builders in builderNames.

    If unable to find an all green build, fall back to the latest known
    revision on this branch, or the tip of the default branch if we don't know
    anything about this branch.

    Also check that we don't schedule a build for a revision that is older that
    the latest revision built on the scheduler's builders.
    """
    def ssFunc(scheduler, t):
        db = scheduler.parent.db

        # Look back 24 hours for a good revision to build
        start = time.time()
        rev = lastGoodRev(db, t, branch, builderNames, start-(24*3600), start)
        end = time.time()
        log.msg("lastGoodRev: took %.2f seconds to run; returned %s" %
                (end-start, rev))

        if rev is None:
            # Couldn't find a good revision.  Fall back to using the latest
            # revision on this branch
            rev = lastChangeset(db, branch)
            log.msg("lastChangeset returned %s" % (rev))

        # Find the last revision our scheduler's builders have built.  This can
        # include forced builds.
        last_built_rev = getLastBuiltRevision(db, t, branch,
                scheduler.builderNames)
        log.msg("lastNightlyRevision was %s" % last_built_rev)

        if last_built_rev is not None:
            # Make sure that rev is newer than the last revision we built.
            later_rev = getLatestRev(db, t, branch, rev, last_built_rev)
            if later_rev != rev:
                log.msg("lastGoodRev: Building %s since it's newer than %s" %
                        (later_rev, rev))
                rev = later_rev
        return SourceStamp(branch=scheduler.branch, revision=rev)
    return ssFunc
