# Additional Scheduler functions
# Contributor(s):
#   Chris AtLee <catlee@mozilla.com>
#   Lukas Blakk <lsblakk@mozilla.com>
from twisted.python import log
from twisted.internet import defer
from twisted.web.client import getPage

import re

import buildbotcustom.try_parser
reload(buildbotcustom.try_parser)

from buildbotcustom.try_parser import TryParser
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
            for change in changes:
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
