#!/usr/bin/env python
import collections
from twisted.trial import unittest

import buildbotcustom.misc as misc
from buildbot.process.properties import Properties

import mock


def makeRequest(builder, _id=None, reason=""):
    request = mock.Mock()
    if not _id:
        _id = id(request)
    request.builder = builder
    request.id = _id
    request.canBeMergedWith = lambda r2: r2.builder.name == builder.name
    request.reason = reason
    request.properties = Properties()
    return request


def makeBuilder(name):
    builder = mock.Mock()
    builder.name = name
    return builder


class TestMergeRequsets(unittest.TestCase):
    def setUp(self):
        # This is basically copied from misc.py
        misc.nomergeBuilders = set()
        misc.builderMergeLimits = collections.defaultdict(lambda: 3)
        misc._mergeCount = 0
        misc._mergeId = None

    def testNoMergeBuilders(self):
        "Tests that nomergeBuilders works"
        b1 = makeBuilder("b1")
        r1 = makeRequest(b1)
        r2 = makeRequest(b1)
        misc.nomergeBuilders = set()

        # b1 isn't in nomergeBuilders, so we can merge the requests
        self.assertTrue(misc.mergeRequests(b1, r1, r2))

        # When b1 is in nomergeBuilders, they're not mergeable
        misc.nomergeBuilders = set(["b1"])
        self.assertFalse(misc.mergeRequests(b1, r1, r2))

    def testMergeLimits(self):
        "Tests that merge limits work"
        b1 = makeBuilder("b1")
        b2 = makeBuilder("b2")

        r1 = makeRequest(b1)
        r2 = makeRequest(b1)
        r3 = makeRequest(b2)
        r4 = makeRequest(b1)
        r5 = makeRequest(b1)

        # We can merge these two (count = 2)
        self.assertTrue(misc.mergeRequests(b1, r1, r2))
        # r3 is on a different builder, so can't merge
        self.assertFalse(misc.mergeRequests(b1, r1, r3))
        # We can merge one more (count = 3)
        self.assertTrue(misc.mergeRequests(b1, r1, r4))
        # Can't merge more than 3
        self.assertFalse(misc.mergeRequests(b1, r1, r5))

    def testResetState(self):
        """Tests that state is reset properly when we're looking at different
        requests"""
        b1 = makeBuilder("b1")
        r1 = makeRequest(b1)
        r2 = makeRequest(b1)
        r3 = makeRequest(b1)

        misc.mergeRequests(b1, r1, r2)
        self.assertEquals(misc._mergeId, r1.id)
        self.assertEquals(misc._mergeCount, 2)

        misc.mergeRequests(b1, r1, r3)
        self.assertEquals(misc._mergeId, r1.id)
        self.assertEquals(misc._mergeCount, 3)

        misc.mergeRequests(b1, r2, r3)
        self.assertEquals(misc._mergeId, r2.id)
        self.assertEquals(misc._mergeCount, 2)

    def testMergeSelfserve(self):
        "Test that having Self-serve in the request reason disables coalescing"
        b1 = makeBuilder("b1")
        r1 = makeRequest(b1)
        r2 = makeRequest(b1, reason="Retriggered via Self-serve")

        self.assertFalse(misc.mergeRequests(b1, r1, r2))
        self.assertFalse(misc.mergeRequests(b1, r2, r1))

    def testMergeNightly(self):
        "Test that jobs with the 'nightly_build' property set to True don't get merged"
        b1 = makeBuilder("b1")
        r1 = makeRequest(b1)
        r2 = makeRequest(b1)
        r2.properties.setProperty('nightly_build', True, 'testharness')

        self.assertFalse(misc.mergeRequests(b1, r1, r2))
        self.assertFalse(misc.mergeRequests(b1, r2, r1))


def main():
    unittest.main()


if __name__ == '__main__':
    main()
