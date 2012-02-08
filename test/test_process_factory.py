import unittest

from buildbotcustom.process.factory import ReleaseUpdatesFactory

class SimpleUpdatesFactory(ReleaseUpdatesFactory):
    def __init__(self, version, releaseChannel, useBetaChannelForRelease):
        self.version = version
        self.releaseChannel = releaseChannel
        self.useBetaChannelForRelease = useBetaChannelForRelease
        self.setChannelData()
    def getSnippetDir(self):
        return 'foo'

class TestReleaseUpdatesFactory(unittest.TestCase):
    def testSetChannelDataWithBetaAndReleaseChannel(self):
        expectedChannels = {
            'betatest': {'dir': 'aus2.test'},
            'releasetest': {'dir': 'aus2.test'},
            'beta': {'dir': 'aus2.beta'},
            'release': {'dir': 'aus2', 'compareTo': 'releasetest'},
        }
        expectedDirMap = {
            'aus2.test': 'foo-test',
            'aus2.beta': 'foo-beta',
            'aus2': 'foo',
        }
        uf = SimpleUpdatesFactory('3.6.26', 'release', True)
        self.assertEqual(uf.channels, expectedChannels)
        self.assertEqual(uf.dirMap, expectedDirMap)
        self.assertEqual(uf.testChannel, 'betatest')

    def testSetChannelDataWithBetaChannelOnly(self):
        expectedChannels = {
            'betatest': {'dir': 'aus2.test'},
            'releasetest': {'dir': 'aus2.test'},
            'beta': {'dir': 'aus2', 'compareTo': 'releasetest'},
        }
        expectedDirMap = {
            'aus2.test': 'foo-test',
            'aus2': 'foo',
        }
        uf = SimpleUpdatesFactory('10.0b6', 'beta', False)
        self.assertEqual(uf.channels, expectedChannels)
        self.assertEqual(uf.dirMap, expectedDirMap)
        self.assertEqual(uf.testChannel, 'betatest')

    def testSetChannelDataWithReleaseChannelOnly(self):
        expectedChannels = {
            'betatest': {'dir': 'aus2.test'},
            'releasetest': {'dir': 'aus2.test'},
            'release': {'dir': 'aus2', 'compareTo': 'releasetest'},
        }
        expectedDirMap = {
            'aus2.test': 'foo-test',
            'aus2': 'foo',
        }
        uf = SimpleUpdatesFactory('9.0.1', 'release', False)
        self.assertEqual(uf.channels, expectedChannels)
        self.assertEqual(uf.dirMap, expectedDirMap)
        self.assertEqual(uf.testChannel, 'betatest')

    def testSetChannelDataWithEsrChannelOnly(self):
        expectedChannels = {
            'betatest': {'dir': 'aus2.test'},
            'releasetest': {'dir': 'aus2.test'},
            'esrtest': {'dir': 'aus2.test'},
            'esr': {'dir': 'aus2', 'compareTo': 'releasetest'},
        }
        expectedDirMap = {
            'aus2.test': 'foo-test',
            'aus2': 'foo',
        }
        uf = SimpleUpdatesFactory('10.0esr', 'esr', False)
        self.assertEqual(uf.channels, expectedChannels)
        self.assertEqual(uf.dirMap, expectedDirMap)
        self.assertEqual(uf.testChannel, 'esrtest')
