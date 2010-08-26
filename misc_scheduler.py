# Additional Scheduler functions
# Contributor(s):
#   Chris AtLee <catlee@mozilla.com>
#   Lukas Blakk <lsblakk@mozilla.com>

from buildbotcustom.try_parser import TryParser

from twisted.python import log
from twisted.internet import defer
from twisted.web.client import getPage
import re

def tryChooser(s, all_changes):
    log.msg("Looking at changes: %s" % all_changes)

    buildersPerChange = {}

    dl = []
    def parseData(data, c):
        # Grab comments, hand off to try_parser
        match = re.search("try:", c.comments)
        if match: 
            customBuilders = TryParser(c.comments, s.builderNames)
        else:
            comments = re.search("<div class=\"page_body\">(.*?)</div>", data, re.M + re.S)
            if not comments:
                # still need to parse a comment string to get the default set
                comments = ""
            else:
                comments = comments.group(1)
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
        # Find out stuff!
        d = getPage(str("http://hg.mozilla.org/try/rev/%s" % c.revision))
      except:
        # if something in getPage errors, we still call parseData with no comments
        log.msg("Error in trying to get request: sending default try set")
        d = defer.succeed("")
      d.addCallback(parseData, c)
      d.addErrback(parseDataError, c)
      dl.append(d)
    d = defer.DeferredList(dl)
    d.addCallback(lambda res: buildersPerChange)
    return d
