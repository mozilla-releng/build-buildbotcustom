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
as on a group of hg repositories. It's polling the json feed of pushlog,
which of the form

{
 "15092": {
  "date": 1281863455,
  "changesets": [
   {
    "node": "ace72819f4a94b9175519a8fa5a1db654edae098",
    "files": [
     "gfx/thebes/gfxBlur.cpp"
    ],
    "tags": [],
    "author": "Julian Seward <jseward@acm.org>",
    "branch": "default",
    "desc": "Bug 582668 - gfxAlphaBoxBlur::Paint appears to pass garbage down through Cairo. r=roc"
   },
   {
    "node": "43b490ef9dab30db2c4e2706110ad5d524a21597",
    "files": [
     "content/html/document/src/nsHTMLDocument.cpp",
     "dom/interfaces/html/nsIDOMNSHTMLDocument.idl",
     "js/src/xpconnect/src/dom_quickstubs.qsconf"
    ],
    "tags": [],
    "author": "Ms2ger <ms2ger@gmail.com>",
    "branch": "default",
    "desc": "Bug 585877 - remove document.height / document.width. r=sicking, sr=jst"
   },
   {
    "node": "75caf7ab03760f6bc39775cd8c4e097f33161c58",
    "files": [
     "modules/plugin/base/src/nsNPAPIPlugin.cpp"
    ],
    "tags": [],
    "author": "Martin Str\u00e1nsk\u00fd <stransky@redhat.com>",
    "branch": "default",
    "desc": "Bug 574354 - Disable OOP for plugins wrapped by nspluginwrapper. r=josh"
   }
  ],
  "user": "dgottwald@mozilla.com"
 }
}
"""

import time
from calendar import timegm
import operator

from twisted.python import log, failure
from twisted.internet import defer, reactor
from twisted.internet.task import LoopingCall
from twisted.web.client import getPage

from buildbot.changes import base, changes
from buildbot.util import json

def _parse_changes(data):
    pushes = json.loads(data)
    changes = []
    for push_id, push_data in pushes.iteritems():
        push_time = push_data['date']
        push_user = push_data['user']
        for cset in push_data['changesets']:
            change = {}
            change['updated'] = push_time
            change['author'] = push_user
            change['changeset'] = cset['node']
            change['files'] = cset['files']
            change['branch'] = cset['branch']
            change['comments'] = cset['desc']
            changes.append(change)

    # Sort by push date
    # Changes in the same push have their order preserved because python list
    # sorts are stable. The leaf of each push is sorted at the end of the list
    # of changes for that push.
    changes.sort(key=lambda c:c['updated'])
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
                 tipsOnly=False, tree=None, repo_branch=None, maxChanges=100):
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
        self.lastChangeset = None
        self.startLoad = 0
        self.loadTime = None
        self.repo_branch = repo_branch
        self.maxChanges = maxChanges

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
            url = "/".join((self.baseURL, 'json-pushes?full=1'))

        args = []
        if self.lastChangeset is not None:
            args.append('fromchange=' + self.lastChangeset)
        if self.tipsOnly:
            args.append('tipsonly=1')
        if args:
            if '?' not in url:
                url += '?'
            else:
                url += '&'
            url += '&'.join(args)

        return str(url)

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
        all_changes = _parse_changes(query)
        if len(all_changes) == 0:
            if self.lastChangeset is None:
                # We don't have a lastChangeset, and there are no changes.  Assume
                # the repository is empty.
                self.emptyRepo = True
                if self.verbose:
                    log.msg("%s is empty" % self.baseURL)
            # Nothing else to do
            return

        # We want to add at most self.maxChanges changes.
        # Go through the list of changes backwards, since we want to keep the
        # latest ones and possibly discard earlier ones.
        change_list = []
        for change in reversed(all_changes):
            if self.maxChanges is not None and len(change_list) >= self.maxChanges:
                break

            # Ignore changes not on the specified in-repo branch.
            if self.repo_branch is not None and self.repo_branch != change['branch']:
                continue
            change_list.append(change)
        # Un-reverse the list of changes so they get added in the right order
        change_list.reverse()

        # If we have a lastChangeset we're comparing against, we've been
        # running for a while and so any changes returned here are new.

        # If the repository was previously empty (indicated by emptyRepo=True),
        # we also want to pay attention to all these pushes.

        # If we don't have a lastChangeset and the repository isn't empty, then
        # don't trigger any new builds, and start monitoring for changes since
        # the latest changeset in the repository
        if self.lastChangeset is not None or self.emptyRepo:
            for change in change_list:
                link = "%s/rev/%s" % (self.baseURL, change["changeset"])
                c = changes.Change(who = change["author"],
                                   files = change["files"],
                                   revision = change["changeset"],
                                   comments = change["comments"],
                                   revlink = link,
                                   when = change["updated"],
                                   branch = self.branch)
                self.changeHook(c)
                self.parent.addChange(c)

        # The repository isn't empty any more!
        self.emptyRepo = False
        # Use the last change found by the poller, regardless of if it's on our
        # branch or not. This is so we don't have to constantly ignore it in
        # future polls.
        self.lastChangeset = all_changes[-1]["changeset"]
        if self.verbose:
            log.msg("last changeset %s on %s" %
                    (self.lastChangeset, self.baseURL))

    def changeHook(self, change):
        pass

class HgPoller(base.ChangeSource, BaseHgPoller):
    """This source will poll a Mercurial server over HTTP using
    the built-in RSS feed for changes and submit them to the
    change master."""

    compare_attrs = ['hgURL', 'branch', 'pollInterval',
                     'pushlogUrlOverride', 'tipsOnly', 'storeRev',
                     'repo_branch']
    parent = None
    loop = None
    volatile = ['loop']

    def __init__(self, hgURL, branch, pushlogUrlOverride=None,
                 tipsOnly=False, pollInterval=30, storeRev=None,
                 repo_branch="default"):
        """
        @type   hgURL:          string
        @param  hgURL:          The base URL of the Hg repo
                                (e.g. http://hg.mozilla.org/)
        @type   branch:         string
        @param  branch:         The branch to check (e.g. mozilla-central)
        @type   pollInterval:   int
        @param  pollInterval:   The time (in seconds) between queries for
                                changes
        @type   tipsOnly:       bool
        @param  tipsOnly:       Make the pushlog only show the tips of pushes.
                                With this enabled every push will only show up
                                as *one* changeset
        @type   storeRev:       string
        @param  storeRev:       A name of a property to set on the resulting
                                Change to help identify the specific repository
                                if multiple HgPollers are used in one branch.
        @type   repo_branch:    string or None
        @param  repo_branch:    Name of the in-repo branch to pay attention to.
                                If None, then pay attention to all branches.
        """

        BaseHgPoller.__init__(self, hgURL, branch, pushlogUrlOverride,
                              tipsOnly, repo_branch=repo_branch)
        self.pollInterval = pollInterval
        self.storeRev = storeRev

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

    def changeHook(self, change):
        if self.storeRev:
            change.properties.setProperty(self.storeRev, change.revision, 'HgPoller')

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
