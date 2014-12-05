from twisted.trial import unittest
import threading
import socket
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

from buildbot.util import json
if not hasattr(json.decoder, 'JSONDecodeError'):
    JSONDecodeError = ValueError
else:
    JSONDecodeError = json.JSONDecodeError

from buildbotcustom.changes import hgpoller


class VerySimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    # This class requires the consumer to set contents, because we
    # cannot override __init__ due to the way HTTPServer works
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(self.contents)
        return

    def log_message(self, fmt, *args):
        pass


class TestHTTPServer(object):

    def __init__(self, contents):
        # Starts up a simple HTTPServer that processes requests with
        # VerySimpleHTTPRequestHandler (subclassed to make sure it's unique for
        # each instance), and serving the contents passed as contents 
        class OurHandler(VerySimpleHTTPRequestHandler):
            pass
        OurHandler.contents = contents
        server = HTTPServer(('', 0), OurHandler)
        ip, port = server.server_address
        def serve_forever_and_catch():
            try:
                server.serve_forever()
            except socket.error:
                pass
        server_thread = threading.Thread(target=serve_forever_and_catch)
        server_thread.setDaemon(True)
        server_thread.start()

        self.server = server
        self.server_thread = server_thread
        self.port = port

    def stop(self):
        # This is ugly.  There's a running thread waiting on a socket.  The
        # call to server_stop here closes that socket, which results in a
        # socket error in the thread, which we catch and then exit.
        self.server.server_close()
        self.server_thread.join()


class UrlCreation(unittest.TestCase):
    def testSimpleUrl(self):
        correctUrl = 'https://hg.mozilla.org/mozilla-central/json-pushes?full=1'
        poller = hgpoller.BaseHgPoller(
            hgURL='https://hg.mozilla.org', branch='mozilla-central')
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testUrlWithLastChangeset(self):
        correctUrl = 'https://hg.mozilla.org/mozilla-central/json-pushes?full=1&fromchange=123456'
        poller = hgpoller.BaseHgPoller(
            hgURL='https://hg.mozilla.org', branch='mozilla-central')
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testTipsOnlyUrl(self):
        correctUrl = 'https://hg.mozilla.org/mozilla-central/json-pushes?full=1&tipsonly=1'
        poller = hgpoller.BaseHgPoller(
            hgURL='https://hg.mozilla.org', branch='mozilla-central',
            tipsOnly=True)
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testTipsOnlyWithLastChangeset(self):
        # there's two possible correct URLs in this case
        correctUrls = [
            'https://hg.mozilla.org/releases/mozilla-1.9.1/json-pushes?full=1&fromchange=123456&tipsonly=1',
            'https://hg.mozilla.org/releases/mozilla-1.9.1/json-pushes?full=1&tipsonly=1&fromchange=123456'
        ]
        poller = hgpoller.BaseHgPoller(hgURL='https://hg.mozilla.org',
                              branch='releases/mozilla-1.9.1', tipsOnly=True)
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessIn(url, correctUrls)

    def testOverrideUrl(self):
        correctUrl = 'https://hg.mozilla.org/other_repo/json-pushes?full=1&fromchange=123456'
        poller = hgpoller.BaseHgPoller(
            hgURL='https://hg.mozilla.org', branch='mozilla-central',
            pushlogUrlOverride='https://hg.mozilla.org/other_repo/json-pushes?full=1')
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testUrlWithUnicodeLastChangeset(self):
        correctUrl = 'https://hg.mozilla.org/mozilla-central/json-pushes?full=1&fromchange=123456'
        poller = hgpoller.BaseHgPoller(
            hgURL='https://hg.mozilla.org', branch='mozilla-central')
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


class RepositoryIndexParsing(unittest.TestCase):
    def testRepositoryIndexParsing(self):
        correctLocales = [('af', 'l10n-central'), ('be', 'l10n-central'),
                          ('de', 'l10n-central'), ('hi', 'l10n-central'),
                          ('kk', 'l10n-central'), ('zh-TW', 'l10n-central')]
        # this must be defined inline, because the `hgpoller` module gets dynamically
        # reloaded via the `misc` module.
        class FakeHgAllLocalesPoller(hgpoller.HgAllLocalesPoller):
            def __init__(self):
                hgpoller.HgAllLocalesPoller.__init__(
                    self, hgURL='fake', repositoryIndex='fake', branch='fake')

            def pollNextLocale(self):
                pass


        poller = FakeHgAllLocalesPoller()
        poller.processData(fakeLocalesFile)
        self.failUnlessEqual(poller.pendingLocales, correctLocales)


class TestPolling(unittest.TestCase):
    def setUp(self):
        x = self.server = TestHTTPServer('testcontents')

    def tearDown(self):
        self.server.stop()

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

            def processData(self, res):
                pass

            def dataFinished(self, res):
                pass

            def dataFailed(self, res):
                pass

            def pollDone(self, res):
                pass

        self.fp = FakePoller()
        d = self.fp.poll()
        d.addCallbacks(self.success, self.failure)
        return d

    def testHgPoller(self):
        url = 'http://localhost:%s' % str(self.server.port)
        return self.doPollingTest(hgpoller.HgPoller, hgURL=url, branch='whatever')

    def testHgLocalePoller(self):
        url = 'http://localhost:%s' % str(self.server.port)
        return self.doPollingTest(hgpoller.HgLocalePoller, locale='fake', parent='fake',
                                  hgURL=url, branch='whatever')

    def testHgAllLocalesPoller(self):
        url = 'http://localhost:%s' % str(self.server.port)
        return self.doPollingTest(hgpoller.HgAllLocalesPoller, hgURL=url,
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
        pushes = hgpoller._parse_changes(validPushlog)
        self.failUnlessEqual(len(pushes), 2)

        self.failUnlessEqual(pushes[0]['changesets'][0]['node'],
                             '4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6')
        self.failUnlessEqual(pushes[0]['date'], 1282358416)
        self.failUnlessEqual(len(pushes[0]['changesets'][0]['files']), 4)
        self.failUnlessEqual(pushes[0]['changesets'][0]['branch'],
                             'GECKO20b5pre_20100820_RELBRANCH')
        self.failUnlessEqual(pushes[0]['changesets'][0]['author'],
                             'Jim Chen <jchen@mozilla.com>')
        self.failUnlessEqual(pushes[0]['user'], 'dougt@mozilla.com')

        self.failUnlessEqual(pushes[1]['changesets'][0]['node'],
                             'ee6fb954cbc3de0f76e84cad6bdff452116e1b03')
        self.failUnlessEqual(pushes[1]['date'], 1282362551)
        self.failUnlessEqual(len(pushes[1]['changesets'][0]['files']), 7)
        self.failUnlessEqual(pushes[1]['changesets'][0]['branch'], 'default')
        self.failUnlessEqual(pushes[1]['user'], 'bobbyholley@stanford.edu')
        self.failUnlessEqual(pushes[1]['changesets'][0]['author'],
                             'Bobby Holley <bobbyholley@gmail.com>')

        self.failUnlessEqual(pushes[1]['changesets'][1]['node'],
                             '33be08836cb164f9e546231fc59e9e4cf98ed991')
        self.failUnlessEqual(len(pushes[1]['changesets'][1]['files']), 1)
        self.failUnlessEqual(pushes[1]['changesets'][1]['branch'], 'default')
        self.failUnlessEqual(pushes[1]['changesets'][1]['author'],
                             'Bobby Holley <bobbyholley@gmail.com>')

    def testMalformedPushlog(self):
        self.failUnlessRaises(
            JSONDecodeError, hgpoller._parse_changes, malformedPushlog)

    def testEmptyPushlog(self):
        self.failUnlessRaises(JSONDecodeError, hgpoller._parse_changes, "")


class RepoBranchHandling(unittest.TestCase):
    def setUp(self):
        self.changes = []

    def doTest(self, repo_branch):
        changes = self.changes

        class TestPoller(hgpoller.BaseHgPoller):
            def __init__(self):
                hgpoller.BaseHgPoller.__init__(self, 'http://localhost', 'whatever',
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

        self.assertEquals(len(self.changes), 2)

    def testDefaultRepoBranch(self):
        self.doTest('default')

        # mergePushChanges is on by default, so we end up with a single change
        # here
        self.assertEquals(len(self.changes), 1)
        self.assertEquals(self.changes[0].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

        titles = self.changes[0].properties.getProperty('commit_titles')
        self.assertEquals(len(titles), 2)
        self.assertEquals(titles[0],
                          'Bug 563088 - Re-enable image discarding.r=joe,a=blocker')
        self.assertEquals(titles[1],
                          'Backout of changesets c866e73f3209 and baff7b7b32bc because of sicking\'s push-and-run bustage.')

    def testRelbranch(self):
        self.doTest('GECKO20b5pre_20100820_RELBRANCH')

        self.assertEquals(len(self.changes), 1)
        self.assertEquals(self.changes[0].revision,
                          '4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6')


class MaxChangesHandling(unittest.TestCase):
    def setUp(self):
        self.changes = []

    def doTest(self, repo_branch, maxChanges, mergePushChanges):
        changes = self.changes

        class TestPoller(hgpoller.BaseHgPoller):
            def __init__(self):
                hgpoller.BaseHgPoller.__init__(self, 'http://localhost', 'whatever',
                                      repo_branch=repo_branch, maxChanges=maxChanges, mergePushChanges=mergePushChanges)
                self.emptyRepo = True

        class parent:
            def addChange(self, change):
                changes.append(change)

        p = TestPoller()
        p.parent = parent()
        p.processData(validPushlog)

    def testNoRepoBigMax(self):
        # Test that we get all of the changes when maxChanges is large enough
        self.doTest(None, 10, False)

        self.assertEquals(len(self.changes), 3)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision,
                          '4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6')
        self.assertEquals(self.changes[1].revision,
                          'ee6fb954cbc3de0f76e84cad6bdff452116e1b03')
        self.assertEquals(self.changes[2].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

    def testMergingNoRepoBigMax(self):
        # Test that we get all of the changes when maxChanges is large enough
        self.doTest(None, 10, True)

        self.assertEquals(len(self.changes), 2)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision,
                          '4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6')
        self.assertEquals(self.changes[1].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

    def testNoRepoUnlimited(self):
        # Test that we get all of the changes when maxChanges is large enough
        self.doTest(None, None, False)

        self.assertEquals(len(self.changes), 3)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision,
                          '4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6')
        self.assertEquals(self.changes[1].revision,
                          'ee6fb954cbc3de0f76e84cad6bdff452116e1b03')
        self.assertEquals(self.changes[2].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

    def testMergingNoRepoUnlimited(self):
        # Test that we get all of the changes when maxChanges is large enough
        self.doTest(None, None, True)

        self.assertEquals(len(self.changes), 2)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision,
                          '4c23e51a484f077ea27af3ea4a4ee13da5aeb5e6')
        self.assertEquals(self.changes[1].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

    def testNoRepoSmallMax(self):
        # Test that we get only 2 changes if maxChanges is set to 2
        self.doTest(None, 2, False)

        # The extra change is the overflow indicator
        self.assertEquals(len(self.changes), 3)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision, None)
        self.assertEquals(self.changes[0].files, ['overflow'])
        self.assertEquals(self.changes[1].revision,
                          'ee6fb954cbc3de0f76e84cad6bdff452116e1b03')
        self.assertEquals(self.changes[2].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

    def testMergingNoRepoSmallMax(self):
        # Test that we get only 1 change if maxChanges is set to 1
        self.doTest(None, 1, True)

        self.assertEquals(len(self.changes), 1)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')
        self.assert_(
            'overflow' in self.changes[0].files, self.changes[0].files)
        self.assert_('widget/src/android/nsWindow.h' not in self.changes[
                     0].files, self.changes[0].files)

    def testDefaultRepoBigMax(self):
        self.doTest('default', 10, False)

        self.assertEquals(len(self.changes), 2)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision,
                          'ee6fb954cbc3de0f76e84cad6bdff452116e1b03')
        self.assertEquals(self.changes[1].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

    def testDefaultRepoSmallMax(self):
        self.doTest('default', 1, False)

        self.assertEquals(len(self.changes), 2)
        # Check that we got the right changes
        self.assertEquals(self.changes[0].revision, None)
        self.assertEquals(self.changes[0].files, ['overflow'])
        self.assertEquals(self.changes[1].revision,
                          '33be08836cb164f9e546231fc59e9e4cf98ed991')

    def testRelbranchSmallMax(self):
        self.doTest('GECKO20b5pre_20100820_RELBRANCH', 1, False)

        self.assertEquals(len(self.changes), 1)
