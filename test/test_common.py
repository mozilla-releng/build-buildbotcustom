import unittest

from buildbotcustom.common import normalizeName, getPreviousVersion


class TestNormalizeName(unittest.TestCase):
    def testNoPrefix(self):
        self.assertEquals(normalizeName('mozilla-beta', min_=1), 'm-beta')

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


class TestGetPreviousVersion(unittest.TestCase):
    def testESR(self):
        self.assertEquals('31.5.3esr',
            getPreviousVersion('31.6.0esr', ['31.5.3esr', '31.5.2esr', '31.4.0esr']))

    def testReleaseBuild1(self):
        self.assertEquals('36.0.4',
            getPreviousVersion('37.0', ['36.0.4', '36.0.1', '35.0.1']))

    def testReleaseBuild2(self):
        self.assertEquals('36.0.4',
            getPreviousVersion('37.0', ['37.0', '36.0.4', '36.0.1', '35.0.1']))

    def testBetaMidCycle(self):
        self.assertEquals('37.0b4',
            getPreviousVersion('37.0b5', ['37.0b4', '37.0b3']))

    def testBetaEarlyCycle(self):
        # 37.0 is the RC build
        self.assertEquals('38.0b1',
            getPreviousVersion('38.0b2', ['38.0b1', '37.0']))

    def testBetaFirstInCycle(self):
        self.assertEquals('37.0',
            getPreviousVersion('38.0b1', ['37.0', '37.0b7']))
