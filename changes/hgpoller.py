# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla-specific Buildbot steps.
#
# The Initial Developer of the Original Code is
# Mozilla Foundation.
# Portions created by the Initial Developer are Copyright (C) 2009
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Axel Hecht <l10n@mozilla.com>
#   Ben Hearsum <bhearsum@mozilla.com>
#   Benjamin Smedberg <benjamin@smedbergs.us>
#   Chris AtLee <catlee@mozilla.com>
#   Chris Cooper <ccooper@deadsquid.com>
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****

"""hgpoller provides Pollers to work on single hg repositories as well
as on a group of hg repositories. It's polling the RSS feed of pushlog,
which is XML of the form

<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <id>http://hg.mozilla.org/l10n-central/repo/pushlog</id>
 <link rel="self" href="http://hg.mozilla.org/l10n-central/repo/pushlog" />
 <updated>2009-02-09T23:10:59Z</updated>
 <title>repo Pushlog</title>
 <entry>
  <title>Changeset 3dd5e26f1334ad08a333a7acbe7649af7450feda</title>
  <id>http://www.selenic.com/mercurial/#changeset-3dd5e26f1334ad08a333a7acbe7649af7450feda</id>
  <link href="http://hg.mozilla.org/l10n-central/repo/rev/3dd5e26f1334ad08a333a7acbe7649af7450feda" />
  <updated>2009-02-09T23:10:59Z</updated>
  <author>
   <name>ldap@domain.tld</name>
  </author>
  <content type="xhtml">
    <div xmlns="http://www.w3.org/1999/xhtml">
      <ul class="filelist"><li class="file">some/file/path</li></ul>
    </div>
  </content>
 </entry>
</feed>
"""

import time
from calendar import timegm
import xml.etree.cElementTree as ElementTree
import operator

from twisted.python import log, failure
from twisted.internet import defer, reactor
from twisted.internet.task import LoopingCall
from twisted.web.client import getPage

from buildbot.changes import base, changes

# From pyiso8601 module,
#  http://code.google.com/p/pyiso8601/source/browse/trunk/iso8601/iso8601.py
#   Revision 22

# Required license header:

# Copyright (c) 2007 Michael Twomey
# 
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
# 
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""ISO 8601 date time string parsing

Basic usage:
>>> import iso8601
>>> iso8601.parse_date("2007-01-25T12:00:00Z")
datetime.datetime(2007, 1, 25, 12, 0, tzinfo=<iso8601.iso8601.Utc ...>)
>>>

"""

from datetime import datetime, timedelta, tzinfo
import re

__all__ = ["parse_timezone", "parse_date", "parse_date_string",
           "ParseError", "Utc", "FixedOffset", 
           "Pluggable", "BasePoller", "BaseHgPoller", "HgPoller",
           "HgLocalePoller", "HgAllLocalesPoller"]

# Adapted from http://delete.me.uk/2005/03/iso8601.html
ISO8601_REGEX = re.compile(r"(?P<year>[0-9]{4})(-(?P<month>[0-9]{1,2})(-(?P<day>[0-9]{1,2})"
    r"((?P<separator>.)(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})(:(?P<second>[0-9]{2})(\.(?P<fraction>[0-9]+))?)?"
    r"(?P<timezone>Z|(([-+])([0-9]{2}):([0-9]{2})))?)?)?)?"
)
TIMEZONE_REGEX = re.compile("(?P<prefix>[+-])(?P<hours>[0-9]{2}).(?P<minutes>[0-9]{2})")

class ParseError(Exception):
    """Raised when there is a problem parsing a date string"""

# Yoinked from python docs
ZERO = timedelta(0)
class Utc(tzinfo):
    """UTC
    
    """
    def utcoffset(self, dt):
        return ZERO

    def tzname(self, dt):
        return "UTC"

    def dst(self, dt):
        return ZERO
UTC = Utc()

class FixedOffset(tzinfo):
    """Fixed offset in hours and minutes from UTC
    
    """
    def __init__(self, offset_hours, offset_minutes, name):
        self.__offset = timedelta(hours=offset_hours, minutes=offset_minutes)
        self.__name = name

    def utcoffset(self, dt):
        return self.__offset

    def tzname(self, dt):
        return self.__name

    def dst(self, dt):
        return ZERO
    
    def __repr__(self):
        return "<FixedOffset %r>" % self.__name

def parse_timezone(tzstring, default_timezone=UTC):
    """Parses ISO 8601 time zone specs into tzinfo offsets
    
    """
    if tzstring == "Z":
        return default_timezone
    # This isn't strictly correct, but it's common to encounter dates without
    # timezones so I'll assume the default (which defaults to UTC).
    # Addresses issue 4.
    if tzstring is None:
        return default_timezone
    m = TIMEZONE_REGEX.match(tzstring)
    prefix, hours, minutes = m.groups()
    hours, minutes = int(hours), int(minutes)
    if prefix == "-":
        hours = -hours
        minutes = -minutes
    return FixedOffset(hours, minutes, tzstring)

def parse_date(datestring, default_timezone=UTC):
    """Parses ISO 8601 dates into datetime objects
    
    The timezone is parsed from the date string. However it is quite common to
    have dates without a timezone (not strictly correct). In this case the
    default timezone specified in default_timezone is used. This is UTC by
    default.
    """
    if not isinstance(datestring, basestring):
        raise ParseError("Expecting a string %r" % datestring)
    m = ISO8601_REGEX.match(datestring)
    if not m:
        raise ParseError("Unable to parse date string %r" % datestring)
    groups = m.groupdict()
    tz = parse_timezone(groups["timezone"], default_timezone=default_timezone)
    if groups["fraction"] is None:
        groups["fraction"] = 0
    else:
        groups["fraction"] = int(float("0.%s" % groups["fraction"]) * 1e6)
    return datetime(int(groups["year"]), int(groups["month"]), int(groups["day"]),
        int(groups["hour"]), int(groups["minute"]), int(groups["second"]),
        int(groups["fraction"]), tz)

# End of iso8601.py

def parse_date_string(dateString):
    return timegm(parse_date(dateString).utctimetuple())

def _parse_changes(query):
    etree = ElementTree.fromstring(query)
    xmlns = re.sub("({.*}).*", "\\1", etree.tag)

    changes = []
    for entry in etree.findall(xmlns+'entry'):
        d = {}
        for n in entry:
            tag = n.tag.replace(xmlns, "")
            if tag == "title":
                d['title'] = n.text
                d['changeset'] = n.text.split(" ")[1]
            elif tag == "updated":
                d['updated'] = parse_date_string(n.text)
            elif tag == "author":
                d['author'] = n[0].text
            elif tag == "link":
                d['link'] = n.get('href')
            elif tag == "content":
                d['files'] = [c.text for c in n.getiterator("{http://www.w3.org/1999/xhtml}li")]
        changes.append(d)
    changes.reverse() # want them in chronological order
    return changes

class Pluggable(object):
    '''The Pluggable class implements a forward for Deferred's that
    can be thrown away.

    This is in particular useful when a network request doesn't really
    error in a reasonable time, and you want to make sure that if it
    answers after you tried to give up on it, it's not confusing the 
    rest of your app by calling back with data twice or something.
    '''
    def __init__(self, d):
        self.d = defer.Deferred()
        self.dead = False
        d.addCallbacks(self.succeeded, self.failed)
    def succeeded(self, result):
        if self.dead:
            log.msg("Dead pluggable got called")
        else:
            self.d.callback(result)
    def failed(self, fail = None):
        if self.dead:
            log.msg("Dead pluggable got errbacked")
        else:
            self.d.errback(fail)

class BasePoller(object):
    attemptLimit = 3
    def __init__(self):
        self.attempts = 0
        self.startLoad = 0
        self.loadTime = None

    def poll(self):
        if self.attempts:
            if self.attempts > self.attemptLimit:
                self.plug.dead = True
                self.attempts = 0
                log.msg("dropping the ball on %s, starting new" % self)
            else:
                self.attempts += 1
                log.msg("Not polling %s because last poll is still working" % self)
                reactor.callLater(0, self.pollDone, None)
                return
        self.attempts = 1
        self.startLoad = time.time()
        self.loadTime = None
        self.plug = Pluggable(self.getData())
        d = self.plug.d
        d.addCallback(self.stopLoad)
        d.addCallback(self.processData)
        d.addCallbacks(self.dataFinished, self.dataFailed)
        d.addCallback(self.pollDone)
        return d

    def stopLoad(self, res):
        self.loadTime = time.time() - self.startLoad
        return res

    def dataFinished(self, res):
        assert self.attempts
        self.attempts = 0

    def dataFailed(self, res):
        assert self.attempts
        self.attempts = 0
        log.msg("%s: polling failed, result %s" % (self, res.value.message))
        res.printTraceback()

    def pollDone(self, res):
        pass



class BaseHgPoller(BasePoller):
    """Common base of HgPoller, HgLocalePoller, and HgAllLocalesPoller.

    Subclasses should implement getData, processData, and __str__"""
    verbose = True
    timeout = 30

    def __init__(self, hgURL, branch, pushlogUrlOverride=None,
                 tipsOnly=False, tree = None):
        BasePoller.__init__(self)
        self.hgURL = hgURL
        self.branch = branch
        self.tree = tree
        if hgURL.endswith("/"):
            hgURL = hgURL[:-1]
        fragments = [hgURL, branch]
        if tree is not None:
            fragments.append(tree)
        self.baseURL = "/".join(fragments)
        self.pushlogUrlOverride = pushlogUrlOverride
        self.tipsOnly = tipsOnly
        self.lastChange = time.time()
        self.lastChangeset = None
        self.startLoad = 0
        self.loadTime = None

        self.emptyRepo = False

    def getData(self):
        url = self._make_url()
        if self.verbose:
            log.msg("Polling Hg server at %s" % url)
        return getPage(url, timeout = self.timeout)

    def _make_url(self):
        url = None
        if self.pushlogUrlOverride:
            url = self.pushlogUrlOverride
        else:
            url = "/".join((self.baseURL, 'pushlog'))

        args = []
        if self.lastChangeset is not None:
            args.append('fromchange=' + self.lastChangeset)
        if self.tipsOnly:
            args.append('tipsonly=1')
        if args:
            url += '?' + '&'.join(args)

        return url

    def dataFailed(self, res):
        if hasattr(res.value, 'status') and res.value.status == '500' and \
                'unknown revision' in res.value.response:
            # Indicates that the revision can't be found.  The repo has most
            # likely been reset.  Forget about our lastChangeset, and set
            # emptyRepo to True so we can trigger builds for new changes there
            if self.verbose:
                log.msg("%s has been reset" % self.baseURL)
            self.lastChangeset = None
            self.emptyRepo = True
        return BasePoller.dataFailed(self, res)

    def processData(self, query):
        change_list = _parse_changes(query)
        if len(change_list) == 0:
            if self.lastChangeset is None:
                # We don't have a lastChangeset, and there are no changes.  Assume
                # the repository is empty.
                self.emptyRepo = True
                if self.verbose:
                    log.msg("%s is empty" % self.baseURL)
            # Nothing else to do
            return

        # If we have a lastChangeset we're comparing against, we've been
        # running for a while and so any changes returned here are new.

        # If the repository was previously empty (indicated by emptyRepo=True),
        # we also want to pay attention to all these pushes.

        # If we don't have a lastChangeset and the repository isn't empty, then
        # don't trigger any new builds, and start monitoring for changes since
        # the latest changeset in the repository
        if self.lastChangeset is not None or self.emptyRepo:
            for change in change_list:
                adjustedChangeTime = change["updated"]
                c = changes.Change(who = change["author"],
                                   files = change["files"],
                                   revision = change["changeset"],
                                   comments = change["link"],
                                   when = adjustedChangeTime,
                                   branch = self.branch)
                self.changeHook(c)
                self.parent.addChange(c)

        # The repository isn't empty any more!
        self.emptyRepo = False
        self.lastChange = max(self.lastChange, *[c["updated"]
                                                 for c in change_list])
        self.lastChangeset = change_list[-1]["changeset"]
        if self.verbose:
            log.msg("last changeset %s on %s" %
                    (self.lastChangeset, self.baseURL))

    def changeHook(self, change):
        pass

class HgPoller(base.ChangeSource, BaseHgPoller):
    """This source will poll a Mercurial server over HTTP using
    the built-in RSS feed for changes and submit them to the
    change master."""

    compare_attrs = ['hgURL', 'branch', 'pollInterval']
    parent = None
    loop = None
    volatile = ['loop']
    
    def __init__(self, hgURL, branch, pushlogUrlOverride=None,
                 tipsOnly=False, pollInterval=30):
        """
        @type   hgURL:          string
        @param  hgURL:          The base URL of the Hg repo
                                (e.g. http://hg.mozilla.org/)
        @type   branch:         string
        @param  branch:         The branch to check (e.g. mozilla-central)
        @type   pollInterval:   int
        @param  pollInterval:   The time (in seconds) between queries for
                                changes
        @type  tipsOnly:        bool
        @param tipsOnly:        Make the pushlog only show the tips of pushes.
                                With this enabled every push will only show up
                                as *one* changeset
        """
        
        BaseHgPoller.__init__(self, hgURL, branch, pushlogUrlOverride,
                              tipsOnly)
        self.pollInterval = pollInterval

    def startService(self):
        self.loop = LoopingCall(self.poll)
        base.ChangeSource.startService(self)
        reactor.callLater(0, self.loop.start, self.pollInterval)

    def stopService(self):
        if self.running:
            self.loop.stop()
        return base.ChangeSource.stopService(self)
    
    def describe(self):
        return "Getting changes from: %s" % self._make_url()

    def __str__(self):
        return "<HgPoller for %s%s>" % (self.hgURL, self.branch)

class HgLocalePoller(BaseHgPoller):
    """This helper class for HgAllLocalesPoller polls a single locale and
    submits changes if necessary."""

    timeout = 30
    verbose = False

    def __init__(self, locale, parent, branch, hgURL):
        BaseHgPoller.__init__(self, hgURL, branch, tree = locale)
        self.locale = locale
        self.parent = parent
        self.branch = branch

    def changeHook(self, change):
        change.properties.setProperty('locale', self.locale, 'HgLocalePoller')

    def pollDone(self, res):
        self.parent.localeDone(self.locale)

    def __str__(self):
        return "<HgLocalePoller for %s>" % self.baseURL

class HgAllLocalesPoller(base.ChangeSource, BasePoller):
    """Poll all localization repositories from an index page.

    For a index page like http://hg.mozilla.org/releases/l10n-mozilla-1.9.1/,
    all links look like /releases/l10n-mozilla-1.9.1/af/, where the last
    path step will be the locale code, and the others will be passed
    as branch for the changes, i.e. 'releases/l10n-mozilla-1.9.1'.
    """

    compare_attrs = ['repositoryIndex', 'pollInterval']
    parent = None
    loop = None
    volatile = ['loop']

    timeout = 10
    parallelRequests = 2
    verboseChilds = False

    def __init__(self, hgURL, repositoryIndex, pollInterval=120):
        """
        @type  repositoryIndex:      string
        @param repositoryIndex:      The URL listing all locale repos
        @type  pollInterval        int
        @param pollInterval        The time (in seconds) between queries for
                                   changes
        """

        BasePoller.__init__(self)
        self.hgURL = hgURL
        if hgURL.endswith("/"):
            hgURL = hgURL[:-1]
        self.repositoryIndex = repositoryIndex
        self.pollInterval = pollInterval
        self.localePollers = {}
        self.locales = []
        self.pendingLocales = []
        self.activeRequests = 0

    def startService(self):
        self.loop = LoopingCall(self.poll)
        base.ChangeSource.startService(self)
        reactor.callLater(0, self.loop.start, self.pollInterval)

    def stopService(self):
        if self.running:
            self.loop.stop()
        return base.ChangeSource.stopService(self)

    def addChange(self, change):
        self.parent.addChange(change)

    def describe(self):
        return "Getting changes from all locales at %s" % self.repositoryIndex

    def getData(self):
        log.msg("Polling all locales at %s/%s/" % (self.hgURL,
                                                  self.repositoryIndex))
        return getPage(self.hgURL + '/' + self.repositoryIndex + '/?style=raw',
                       timeout = self.timeout)

    def getLocalePoller(self, locale, branch):
        if (locale, branch) not in self.localePollers:
            lp = HgLocalePoller(locale, self, branch,
                                self.hgURL)
            lp.verbose = self.verboseChilds
            self.localePollers[(locale, branch)] = lp
        return self.localePollers[(locale, branch)]

    def processData(self, data):
        locales = filter(None, data.split())
        # get locales and branches
        def brancher(link):
            steps = filter(None, link.split('/'))
            loc = steps.pop()
            branch = '/'.join(steps)
            return (loc, branch)
        # locales is now locale code / branch tuple
        locales = map(brancher, locales)
        if locales != self.locales:
            log.msg("new locale list: " + " ".join(map(str, locales)))
        self.locales = locales
        self.pendingLocales = locales[:]
        # prune removed locales from pollers
        for oldLoc in self.localePollers.keys():
            if oldLoc not in locales:
                self.localePollers.pop(oldLoc)
                log.msg("not polling %s on %s anymore, dropped from repositories" %
                        oldLoc)
        for i in xrange(self.parallelRequests):
            self.activeRequests += 1
            reactor.callLater(0, self.pollNextLocale)

    def pollNextLocale(self):
        if not self.pendingLocales:
            self.activeRequests -= 1
            if not self.activeRequests:
                msg = "%s done with all locales" % str(self)
                loadTimes = map(lambda p: p.loadTime, self.localePollers.values())
                goodTimes = filter(lambda t: t is not None, loadTimes)
                if not goodTimes:
                    msg += ". All %d locale pollers failed" % len(loadTimes)
                else:
                    msg += ", min: %.1f, max: %.1f, mean: %.1f" % \
                        (min(goodTimes), max(goodTimes), 
                         sum(goodTimes) / len(goodTimes))
                    if len(loadTimes) > len(goodTimes):
                        msg += ", %d failed" % (len(loadTimes) - len(goodTimes))
                log.msg(msg)
                log.msg("Total time: %.1f" % (time.time() - self.startLoad))
            return
        loc, branch = self.pendingLocales.pop(0)
        poller = self.getLocalePoller(loc, branch)
        poller.poll()

    def localeDone(self, loc):
        if self.verboseChilds:
            log.msg("done with " + loc)
        reactor.callLater(0, self.pollNextLocale)        

    def __str__(self):
        return "<HgAllLocalesPoller for %s/%s/>" % (self.hgURL,
                                                   self.repositoryIndex)
