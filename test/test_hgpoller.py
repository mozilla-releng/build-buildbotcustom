from twisted.internet import defer, reactor
from twisted.trial import unittest

from buildbot.util import json

from buildbotcustom.changes.hgpoller import BasePoller, BaseHgPoller, HgPoller, \
  HgLocalePoller, HgAllLocalesPoller, _parse_changes
from buildbotcustom.test.utils import startHTTPServer

class UrlCreation(unittest.TestCase):
    def testSimpleUrl(self):
        correctUrl = 'http://hg.mozilla.org/mozilla-central/json-pushes?full=1'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central')
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testUrlWithLastChangeset(self):
        correctUrl = 'http://hg.mozilla.org/mozilla-central/json-pushes?full=1&fromchange=123456'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central')
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testTipsOnlyUrl(self):
        correctUrl = 'http://hg.mozilla.org/mozilla-central/json-pushes?full=1&tipsonly=1'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central',
                              tipsOnly=True)
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testTipsOnlyWithLastChangeset(self):
        # there's two possible correct URLs in this case
        correctUrls = [
          'http://hg.mozilla.org/releases/mozilla-1.9.1/json-pushes?full=1&fromchange=123456&tipsonly=1',
          'http://hg.mozilla.org/releases/mozilla-1.9.1/json-pushes?full=1&tipsonly=1&fromchange=123456'
        ]
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org',
                              branch='releases/mozilla-1.9.1', tipsOnly=True)
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessIn(url, correctUrls)

    def testOverrideUrl(self):
        correctUrl = 'http://hg.mozilla.org/other_repo/json-pushes?full=1&fromchange=123456'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central',
            pushlogUrlOverride='http://hg.mozilla.org/other_repo/json-pushes?full=1')
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testUrlWithUnicodeLastChangeset(self):
        correctUrl = 'http://hg.mozilla.org/mozilla-central/json-pushes?full=1&fromchange=123456'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central')
        poller.lastChangeset = u'123456'
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)
        self.failUnless(isinstance(url, str))


fakeLocalesFile = """/l10n-central/af/
/l10n-central/be/
/l10n-central/de/
/l10n-central/hi/
/l10n-central/kk/
/l10n-central/zh-TW/"""

class FakeHgAllLocalesPoller(HgAllLocalesPoller):
    def __init__(self):
        HgAllLocalesPoller.__init__(self, hgURL='fake', repositoryIndex='fake')

    def pollNextLocale(self):
        pass

class RepositoryIndexParsing(unittest.TestCase):
    def testRepositoryIndexParsing(self):
        correctLocales = [('af', 'l10n-central'), ('be', 'l10n-central'),
                          ('de', 'l10n-central'), ('hi', 'l10n-central'),
                          ('kk', 'l10n-central'), ('zh-TW', 'l10n-central')]
        poller = FakeHgAllLocalesPoller()
        poller.processData(fakeLocalesFile)
        self.failUnlessEqual(poller.pendingLocales, correctLocales)


class TestPolling(unittest.TestCase):
    def setUp(self):
        self.server, self.portnum = startHTTPServer('testcontents')

    def tearDown(self):
        self.server.server_close()
        self.portnum = None
        self.server = None

    def success(self, res):
        self.failUnless(self.fp.success)

    def failure(self, res):
        print res
        self.fail()

    def doPollingTest(self, poller, **kwargs):
        class FakePoller(poller):
            def __init__(self):
                poller.__init__(self, **kwargs)
                self.success = False
            def stopLoad(self, res):
                self.success = True
            def processData(self, res): pass
            def dataFinished(self, res): pass
            def dataFailed(self, res): pass
            def pollDone(self, res): pass

        self.fp = FakePoller()
        d = self.fp.poll()
        d.addCallbacks(self.success, self.failure)
        return d

    def testHgPoller(self):
        url = 'http://localhost:%s' % str(self.portnum)
        return self.doPollingTest(HgPoller, hgURL=url, branch='whatever')

    def testHgLocalePoller(self):
        url = 'http://localhost:%s' % str(self.portnum)
        return self.doPollingTest(HgLocalePoller, locale='fake', parent='fake',
                                  hgURL=url, branch='whatever')

    def testHgAllLocalesPoller(self):
        url = 'http://localhost:%s' % str(self.portnum)
        return self.doPollingTest(HgAllLocalesPoller, hgURL=url,
                                  repositoryIndex='foobar')


validPushlog = """
{
 "15226": {
  "date": 1282358416,
  "changesets": [
   {
    "node": "4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6",
    "files": [
     "embedding/android/GeckoInputConnection.java",
     "embedding/android/GeckoSurfaceView.java",
     "widget/src/android/nsWindow.cpp",
     "widget/src/android/nsWindow.h"
    ],
    "tags": [],
    "author": "Jim Chen <jchen@mozilla.com>",
    "branch": "GECKO20b5pre_20100820_RELBRANCH",
    "desc": "Bug 588456 - Properly commit Android IME composition on blur; r=mwu a=blocking-fennec"
   }
  ],
  "user": "dougt@mozilla.com"
 },
 "15227": {
  "date": 1282362551,
  "changesets": [
   {
    "node": "ee6fb954cbc3de0f76e84cad6bdff452116e1b03",
    "files": [
     "browser/base/content/browser.js",
     "browser/components/privatebrowsing/content/aboutPrivateBrowsing.xhtml",
     "browser/locales/en-US/chrome/overrides/netError.dtd",
     "build/automation.py.in",
     "docshell/resources/content/netError.xhtml",
     "dom/locales/en-US/chrome/netErrorApp.dtd",
     "extensions/cookie/nsPermissionManager.cpp"
    ],
    "tags": [],
    "author": "Bobby Holley <bobbyholley@gmail.com>",
    "branch": "default",
    "desc": "Backout of changesets c866e73f3209 and baff7b7b32bc because of sicking's push-and-run bustage. a=backout"
   },
   {
    "node": "33be08836cb164f9e546231fc59e9e4cf98ed991",
    "files": [
     "modules/libpref/src/init/all.js"
    ],
    "tags": [],
    "author": "Bobby Holley <bobbyholley@gmail.com>",
    "branch": "default",
    "desc": "Bug 563088 - Re-enable image discarding.r=joe,a=blocker"
   }
  ],
  "user": "bobbyholley@stanford.edu"
 }
}
"""

malformedPushlog = """
{
 "15226": {
  "date": 1282358416,
  "changesets": [
   {
    "node": "4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6",
    "files": [
     "embedding/android/GeckoInputConnection.java",
     "embedding/android/GeckoSurfaceView.java",
     "widget/src/android/nsWindow.cpp",
     "widget/src/android/nsWindow.h"
    ],
    "tags": [],
    "author": "Jim Chen <jchen@mozilla.com>",
    "branch": "GECKO20b5pre_20100820_RELBRANCH",
    "desc": "Bug 588456 - Properly commit Android IME composition on blur; r=mwu a=blocking-fennec"
   }
  ],
  "user": "dougt@mozilla.com"
"""

class PushlogParsing(unittest.TestCase):
    def testValidPushlog(self):
        changes = _parse_changes(validPushlog)
        self.failUnlessEqual(len(changes), 3)

        self.failUnlessEqual(changes[0]['changeset'], '4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6')
        self.failUnlessEqual(changes[0]['updated'], 1282358416)
        self.failUnlessEqual(len(changes[0]['files']), 4)
        self.failUnlessEqual(changes[0]['branch'], 'GECKO20b5pre_20100820_RELBRANCH')
        self.failUnlessEqual(changes[0]['author'], 'dougt@mozilla.com')

        self.failUnlessEqual(changes[1]['changeset'], 'ee6fb954cbc3de0f76e84cad6bdff452116e1b03')
        self.failUnlessEqual(changes[1]['updated'], 1282362551)
        self.failUnlessEqual(len(changes[1]['files']), 7)
        self.failUnlessEqual(changes[1]['branch'], 'default')
        self.failUnlessEqual(changes[1]['author'], 'bobbyholley@stanford.edu')

        self.failUnlessEqual(changes[2]['changeset'], '33be08836cb164f9e546231fc59e9e4cf98ed991')
        self.failUnlessEqual(changes[2]['updated'], 1282362551)
        self.failUnlessEqual(len(changes[2]['files']), 1)
        self.failUnlessEqual(changes[2]['branch'], 'default')
        self.failUnlessEqual(changes[2]['author'], 'bobbyholley@stanford.edu')

    def testMalformedPushlog(self):
        self.failUnlessRaises(json.decoder.JSONDecodeError, _parse_changes, malformedPushlog)

    def testEmptyPushlog(self):
        self.failUnlessRaises(json.decoder.JSONDecodeError, _parse_changes, "")

class RepoBranchHandling(unittest.TestCase):
    def setUp(self):
        self.changes = []

    def doTest(self, repo_branch):
        changes = self.changes
        class TestPoller(BaseHgPoller):
            def __init__(self):
                BaseHgPoller.__init__(self, 'http://localhost', 'whatever',
                        repo_branch=repo_branch)
                self.emptyRepo = True

        class parent:
            def addChange(self, change):
                changes.append(change)

        p = TestPoller()
        p.parent = parent()
        p.processData(validPushlog)

    def testNoRepoBranch(self):
        self.doTest(None)

        self.assertEquals(len(self.changes), 3)

    def testDefaultRepoBranch(self):
        self.doTest('default')

        self.assertEquals(len(self.changes), 2)

    def testRelbranch(self):
        self.doTest('GECKO20b5pre_20100820_RELBRANCH')

        self.assertEquals(len(self.changes), 1)
