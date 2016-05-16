from twisted.trial import unittest

from buildbotcustom.misc import makeImportantFunc, \
    changeContainsScriptRepoRevision

from buildbot.process.properties import Properties


class Change(object):
    files = []
    revlink = None
    comments = ""
    revision = None
    properties = Properties()

    def __init__(self, files=None, revlink=None, comments=None, revision=None,
                 properties=None):
        if files:
            self.files = files
        if revlink:
            self.revlink = revlink
        if comments:
            self.comments = comments
        if revision:
            self.revision = revision
        if properties:
            self.properties = properties


class TestProductImportance(unittest.TestCase):
    def testImportant(self):
        f = makeImportantFunc(
            'https://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(revlink="https://hg.mozilla.org/mozilla-central/rev/1234",
                   files=['browser/foo', 'mobile/bar'])
        self.assertTrue(f(c))

    def testUnImportant(self):
        f = makeImportantFunc(
            'https://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(revlink="https://hg.mozilla.org/mozilla-central/rev/1234",
                   files=['android/foo', 'mobile/bar'])
        self.assertFalse(f(c))

    def testDontBuild(self):
        f = makeImportantFunc(
            'https://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(
            revlink="https://hg.mozilla.org/mozilla-central/rev/1234",
            files=['browser/foo', 'mobile/bar'], comments="DONTBUILD me")
        self.assertFalse(f(c))

    def testNonpollerChange(self):
        f = makeImportantFunc(
            'https://hg.mozilla.org/mozilla-central', 'firefox')
        c = Change(revlink="", files=['browser/foo', 'mobile/bar'])
        self.assertFalse(f(c))

    def testImportantNoProduct(self):
        f = makeImportantFunc('https://hg.mozilla.org/mozilla-central', None)
        c = Change(revlink="https://hg.mozilla.org/mozilla-central/rev/1234",
                   files=['browser/foo', 'mobile/bar'])
        self.assertTrue(f(c))


class TestChangeContainsScriptRepoRevision(unittest.TestCase):

    def test_exact_match(self):
        c = Change(properties=Properties(
            script_repo_revision="F_34_0_RELEASE"))
        self.assertTrue(changeContainsScriptRepoRevision(c, "F_34_0_RELEASE"))

    def test_partial_match(self):
        c = Change(properties=Properties(
            script_repo_revision="F_34_0_5_RELEASE"))
        self.assertFalse(changeContainsScriptRepoRevision(c, "F_34_0_RELEASE"))
