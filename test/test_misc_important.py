from twisted.trial import unittest

from buildbotcustom.misc import makeImportantFunc


class Change(object):
    files = []
    revlink = None
    comments = ""
    revision = None

    def __init__(self, files=None, revlink=None, comments=None, revision=None):
        if files:
            self.files = files
        if revlink:
            self.revlink = revlink
        if comments:
            self.comments = comments
        if revision:
            self.revision = revision


class TestProductImportance(unittest.TestCase):
    def testImportant(self):
        f = makeImportantFunc(
            'http://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(revlink="http://hg.mozilla.org/mozilla-central/rev/1234",
                   files=['browser/foo', 'mobile/bar'])
        self.assertTrue(f(c))

    def testUnImportant(self):
        f = makeImportantFunc(
            'http://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(revlink="http://hg.mozilla.org/mozilla-central/rev/1234",
                   files=['b2g/foo', 'mobile/bar'])
        self.assertFalse(f(c))

    def testDontBuild(self):
        f = makeImportantFunc(
            'http://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(revlink="http://hg.mozilla.org/mozilla-central/rev/1234", files=['browser/foo', 'mobile/bar'], comments="DONTBUILD me")
        self.assertFalse(f(c))

    def testNonpollerChange(self):
        f = makeImportantFunc(
            'http://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(revlink="", files=['browser/foo', 'mobile/bar'])
        self.assertFalse(f(c))

    def testImportantNoProduct(self):
        f = makeImportantFunc('http://hg.mozilla.org/mozilla-central', None)
        c = Change(revlink="http://hg.mozilla.org/mozilla-central/rev/1234",
                   files=['browser/foo', 'mobile/bar'])
        self.assertTrue(f(c))
