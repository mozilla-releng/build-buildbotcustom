import unittest

from buildbotcustom.common import normalizeName


class TestNormalizeName(unittest.TestCase):
    def testNoPrefix(self):
        self.assertEquals(normalizeName('mozilla-beta', min_=1), 'm-beta')

    def testPrefix(self):
        self.assertEquals(normalizeName('comm-release', product='thunderbird', min_=1), 'tb-c-rel')

    def testExclusiveWordReplacement(self):
        self.assertEquals(normalizeName('errasdebug', min_=1), 'errasdebug')

    def testExpandShort(self):
        self.assertEquals(normalizeName('mozilla-central', min_=8, max_=8), 'm-cen-00')

    def testCantShorten(self):
        self.assertRaises(ValueError, normalizeName, 'abcdefgh', max_=2)

    def testPrefixDoesntViolateMax(self):
        got = normalizeName('comm-beta', product='thunderbird', min_=9, max_=9)
        self.assertEquals(got, 'tb-c-beta')

    def testPrefixDelimeterOnly(self):
        got = normalizeName('mozilla-release', min_=6, max_=6)
        self.assertEquals(got, 'm-rel-')
