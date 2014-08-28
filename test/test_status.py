from twisted.trial import unittest
from buildbot.changes import changes

from buildbotcustom.status.generators import getSensibleCommitTitle

SENSIBLE_TITLE_TESTCASES = [
    ['Bug 1', ['Bug 1']],
    ['Bug 1', ['Bug 1', 'Bug 2']],

    ['Bug 1', ['try: -b d -p all', 'Bug 1']],
    ['Bug 1', ['try: -b d -p all', 'try: -b d -p none', 'Bug 1']],

    ['test.patch', ['[mq]: test.patch']],
    ['test.patch', ['imported patch test.patch']],
    ['test imported patch test.patch', ['test imported patch test.patch']],

    ['Bug 1 - Test', ['Bug 1 - Test; r=me']],
    ['Bug 1 - Test', ['Bug 1 - Test. r?me f=you']],

    ['Bug 1', [' Bug 1;,.- ']],
]

class TestGenerator(unittest.TestCase):
    def testGetSensibleCommitTitle(self):
        for case in SENSIBLE_TITLE_TESTCASES:
            self.assertEquals(getSensibleCommitTitle(case[1]), case[0])
