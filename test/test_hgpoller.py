from xml.parsers.expat import ExpatError

from twisted.internet import defer, reactor
from twisted.trial import unittest

from buildbotcustom.changes.hgpoller import BaseHgPoller, \
  BaseHgAllLocalesPoller, BuildbotHgPoller, BuildbotHgLocalePoller, \
  _parse_changes
from buildbotcustom.test.utils import startHTTPServer


class UrlCreation(unittest.TestCase):
    def testSimpleUrl(self):
        correctUrl = 'http://hg.mozilla.org/mozilla-central/pushlog'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central')
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testUrlWithLastChangeset(self):
        correctUrl = 'http://hg.mozilla.org/mozilla-central/pushlog?fromchange=123456'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central')
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testTipsOnlyUrl(self):
        correctUrl = 'http://hg.mozilla.org/mozilla-central/pushlog?tipsonly=1'
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org', branch='mozilla-central',
                              tipsOnly=True)
        url = poller._make_url()
        self.failUnlessEqual(url, correctUrl)

    def testTipsOnlyWithLastChangeset(self):
        # there's two possible correct URLs in this case
        correctUrls = [
          'http://hg.mozilla.org/releases/mozilla-1.9.1/pushlog?fromchange=123456&tipsonly=1',
          'http://hg.mozilla.org/releases/mozilla-1.9.1/pushlog?tipsonly=1&fromchange=123456'
        ]
        poller = BaseHgPoller(hgURL='http://hg.mozilla.org',
                              branch='releases/mozilla-1.9.1', tipsOnly=True)
        poller.lastChangeset = '123456'
        url = poller._make_url()
        self.failUnlessIn(url, correctUrls)


fakeLocalesFile = """/l10n-central/af/
/l10n-central/be/
/l10n-central/de/
/l10n-central/hi/
/l10n-central/kk/
/l10n-central/zh-TW/"""

class FakeHgAllLocalesPoller(BaseHgAllLocalesPoller):
    def __init__(self):
        BaseHgAllLocalesPoller.__init__(self, hgURL='fake',
                                        repositoryIndex='fake')

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

    def testBuildbotHgPoller(self):
        url = 'http://localhost:%s' % str(self.portnum)
        return self.doPollingTest(BuildbotHgPoller, hgURL=url,
                                  branch='whatever')

    def testBuildbotHgLocalePoller(self):
        url = 'http://localhost:%s' % str(self.portnum)
        return self.doPollingTest(BuildbotHgLocalePoller, locale='fake',
                                  parent='fake', hgURL=url, branch='whatever')

    def testBaseHgAllLocalesPoller(self):
        url = 'http://localhost:%s' % str(self.portnum)
        return self.doPollingTest(BaseHgAllLocalesPoller, hgURL=url,
                                  repositoryIndex='foobar')


validPushlog = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <id>http://hg.mozilla.org/mozilla-central/pushlog</id>
 <link rel="self" href="http://hg.mozilla.org/mozilla-central/pushlog"/>
 <link rel="alternate" href="http://hg.mozilla.org/mozilla-central/pushloghtml"/>
 <title>mozilla-central Pushlog</title>
 <updated>2009-08-27T17:31:43Z</updated>
 <entry>
  <title>Changeset 9faf54d97833ccfd3fb28b6d92299124b22f555b</title>
  <id>http://www.selenic.com/mercurial/#changeset-9faf54d97833ccfd3fb28b6d92299124b22f555b</id>
  <link href="http://hg.mozilla.org/mozilla-central/rev/9faf54d97833ccfd3fb28b6d92299124b22f555b"/>
  <updated>2009-08-27T16:09:42Z</updated>
  <author>
   <name>bsmedberg@mozilla.com</name>
  </author>
  <content type="xhtml">
   <div xmlns="http://www.w3.org/1999/xhtml">
    <ul class="filelist"><li class="file">browser/app/Makefile.in</li><li class="file">browser/components/shell/src/Makefile.in</li><li class="file">build/pymake/pymake/parserdata.py</li><li class="file">toolkit/components/build/Makefile.in</li><li class="file">toolkit/components/downloads/src/Makefile.in</li><li class="file">toolkit/library/Makefile.in</li><li class="file">widget/tests/Makefile.in</li></ul>
   </div>
  </content>
 </entry>
 <entry>
  <title>Changeset cc3240bc917a1ca9cd75044e2291b51ab46349f2</title>
  <id>http://www.selenic.com/mercurial/#changeset-cc3240bc917a1ca9cd75044e2291b51ab46349f2</id>
  <link href="http://hg.mozilla.org/mozilla-central/rev/cc3240bc917a1ca9cd75044e2291b51ab46349f2"/>
  <updated>2009-08-27T15:57:21Z</updated>
  <author>
   <name>bsmedberg@mozilla.com</name>
  </author>
  <content type="xhtml">
   <div xmlns="http://www.w3.org/1999/xhtml">
    <ul class="filelist"><li class="file">accessible/src/html/Makefile.in</li><li class="file">accessible/src/xforms/Makefile.in</li><li class="file">accessible/src/xul/Makefile.in</li><li class="file">browser/app/Makefile.in</li><li class="file">browser/components/build/Makefile.in</li><li class="file">browser/components/shell/src/Makefile.in</li><li class="file">build/pymake/pymake/parserdata.py</li><li class="file">build/wince/shunt/Makefile.in</li><li class="file">content/base/src/Makefile.in</li><li class="file">content/xslt/src/base/Makefile.in</li><li class="file">content/xslt/src/xml/Makefile.in</li><li class="file">content/xslt/src/xpath/Makefile.in</li><li class="file">content/xslt/src/xslt/Makefile.in</li><li class="file">dom/src/storage/Makefile.in</li><li class="file">embedding/browser/activex/src/plugin/Makefile.in</li><li class="file">embedding/browser/gtk/src/Makefile.in</li><li class="file">embedding/browser/webBrowser/Makefile.in</li><li class="file">extensions/pref/autoconfig/src/Makefile.in</li><li class="file">gfx/src/Makefile.in</li><li class="file">gfx/src/thebes/Makefile.in</li><li class="file">gfx/thebes/src/Makefile.in</li><li class="file">intl/uconv/src/Makefile.in</li><li class="file">intl/uconv/tests/Makefile.in</li><li class="file">js/jsd/Makefile.in</li><li class="file">js/src/xpconnect/src/Makefile.in</li><li class="file">layout/base/Makefile.in</li><li class="file">layout/build/Makefile.in</li><li class="file">layout/forms/Makefile.in</li><li class="file">layout/generic/Makefile.in</li><li class="file">layout/inspector/src/Makefile.in</li><li class="file">layout/tables/Makefile.in</li><li class="file">layout/xul/base/src/Makefile.in</li><li class="file">modules/libpref/src/Makefile.in</li><li class="file">modules/plugin/base/src/Makefile.in</li><li class="file">netwerk/build/Makefile.in</li><li class="file">netwerk/cache/src/Makefile.in</li><li class="file">toolkit/components/build/Makefile.in</li><li class="file">toolkit/library/Makefile.in</li><li class="file">toolkit/mozapps/update/src/updater/Makefile.in</li><li class="file">toolkit/system/gnome/Makefile.in</li><li class="file">toolkit/xre/Makefile.in</li><li class="file">uriloader/exthandler/Makefile.in</li><li class="file">widget/src/cocoa/Makefile.in</li><li class="file">widget/src/gtk2/Makefile.in</li><li class="file">widget/src/xpwidgets/Makefile.in</li><li class="file">xpcom/base/Makefile.in</li><li class="file">xpcom/build/Makefile.in</li><li class="file">xpcom/glue/Makefile.in</li><li class="file">xpcom/obsolete/Makefile.in</li><li class="file">xpfe/browser/src/Makefile.in</li></ul>
   </div>
  </content>
 </entry>

</feed>
"""

malformedPushlog = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <id>http://hg.mozilla.org/mozilla-central/pushlog</id>
 <link rel="self" href="http://hg.mozilla.org/mozilla-central/pushlog"/>
 <link rel="alternate" href="http://hg.mozilla.org/mozilla-central/pushloghtml"/>
 <title>mozilla-central Pushlog</title>
 <updated>2009-08-27T17:31:43Z</updated>
 <entry>
  <title>Changeset 82e2e7d9c18f75545e6cd3e9c187e7c881484d4a</title>
  <id>http://www.selenic.com/mercurial/#changeset-82e2e7d9c18f75545e6cd3e9c187e7c881484d4a</id>
  <link href="http://hg.mozilla.org/mozilla-central/rev/82e2e7d9c18f75545e6cd3e9c187e7c881484d4a"/>
  <updated>2009-08-27T17:31:43Z</updated>
  <author>
   <name>blassey@mozilla.com</name>
  </author>
  <content type="xhtml">
   <div xmlns="http://www.w3.org/1999/xhtml">
    <ul class="filelist"><li class="file">widget/src/windows/nsWindow.cpp</li><li class="file">widget/src/windows/nsWindow.h</li><li class="file">widget/src/windows/nsWindowGfx.cpp</li></ul>
   </div>
  </content>
 </entry>
"""

class PushlogParsing(unittest.TestCase):
    def testValidPushlog(self):
        changes = _parse_changes(validPushlog)
        self.failUnlessEqual(len(changes), 2)
        self.failUnlessEqual(changes[0]['changeset'], 'cc3240bc917a1ca9cd75044e2291b51ab46349f2')
        self.failUnlessEqual(changes[0]['updated'], 1251388641)
        self.failUnlessEqual(len(changes[0]['files']), 50)
        self.failUnlessEqual(changes[1]['changeset'], '9faf54d97833ccfd3fb28b6d92299124b22f555b')
        self.failUnlessEqual(changes[1]['updated'], 1251389382)
        self.failUnlessEqual(len(changes[1]['files']), 7)

    def testMalformedPushlog(self):
        self.failUnlessRaises(ExpatError, _parse_changes, malformedPushlog)

    def testEmptyPushlog(self):
        self.failUnlessRaises(ExpatError, _parse_changes, "")
