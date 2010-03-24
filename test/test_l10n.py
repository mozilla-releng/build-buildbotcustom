# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is l10n test automation.
#
# The Initial Developer of the Original Code is
# Mozilla Foundation
# Portions created by the Initial Developer are Copyright (C) 2008
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#	Axel Hecht <l10n@mozilla.com>
#       Chris AtLee <catlee@mozilla.com>
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****

'''Tests for buildbot integration on l10n builds.

The tests in this module use the helpers from utils to test
the l10n build logic. For each scenario, a number of changes is
fed to the system, and after the dummy builds are completed,
both the buildbot status and the database are checked for
expected results.
'''

from twisted.trial import unittest
from twisted.internet import defer

from buildbot.broken_test.runutils import MasterMixin, StallMixin
from buildbot.changes.changes import Change

from buildbotcustom.l10n import ParseLocalesFile, L10nMixin
from buildbotcustom.test.utils import startHTTPServer

locales1 = """\
en-US
ja
ja-JP-mac osx
"""
class TestLocales(unittest.TestCase):
    def setUp(self):
        self.server, self.port = startHTTPServer(locales1)

    def tearDown(self):
        self.server.server_close()

    def testParseLocales(self):
        self.failUnlessEqual(ParseLocalesFile(locales1),
            {'en-US': [], 'ja': [], 'ja-JP-mac': ['osx']})

    def testGetLocales(self):
        l = L10nMixin('linux', repo='http://localhost:%s/' % self.port)
        d = defer.maybeDeferred(l.getLocales)
        def _check(locales):
            self.failUnlessEqual(locales,
                {'en-US': [], 'ja': [], 'ja-JP-mac': ['osx']})
        d.addCallback(_check)
        return d

depend_config = """\
from buildbot.buildslave import BuildSlave
from buildbot.steps import dummy
from buildbot.process import factory
from buildbot.config import BuilderConfig
from buildbot.scheduler import Scheduler
BuildmasterConfig = c = {}

c['slaves'] = [BuildSlave('bot1', 'sekrit')]
c['slavePortnum'] = 0
c['builders'] = []

dummy_factory = factory.BuildFactory([dummy.Dummy(timeout=1)])
c['builders'].append(BuilderConfig('dummy1', slavename='bot1', factory=dummy_factory))
c['builders'].append(BuilderConfig('dummy2', slavename='bot1', factory=dummy_factory))

locales = {'en-US': [],
           'fr': ['linux'],
           'es': ['osx'],
           'en-GB': [],
          }

from buildbotcustom.l10n import DependentL10n
s1 = Scheduler('upstream', None, None, ['dummy1'])
s2 = DependentL10n('downstream', s1, ['dummy2'], 'linux', locales=locales)

c['schedulers'] = [s1, s2]
c['mergeRequests'] = lambda builder, req1, req2: False
"""

class TestDependentL10n(MasterMixin, StallMixin, unittest.TestCase):
    def testParse(self):
        self.basedir = "l10n/dependent/parse"
        self.create_master()
        d = self.master.loadConfig(depend_config)
        return d

    def testRun(self):
        self.basedir = "l10n/dependent/run"
        self.create_master()
        d = self.master.loadConfig(depend_config)

        d.addCallback(lambda ign: self.connectSlave(['dummy1', 'dummy2']))

        def _connected(ign):
            c = Change("foo", [], "bar")
            self.master.change_svc.addChange(c)
        d.addCallback(_connected)

        d.addCallback(self.stall, 5)

        def _check(ign):
            # dummy1 should have one build
            b = self.status.getBuilder('dummy1').getLastFinishedBuild()
            self.failUnless(b)
            self.failUnlessEqual(b.getNumber(), 0)

            # dummy2 should have 2 builds (en-GB, fr, but not en-US or es)
            # en-US doesn't need repacking, and es doesn't have linux
            b = self.status.getBuilder('dummy2').getLastFinishedBuild()
            self.failUnless(b)
            self.failUnlessEqual(b.getNumber(), 1)

        d.addCallback(_check)

        return d

trigger_config = """\
from buildbot.buildslave import BuildSlave
from buildbot.steps import dummy, trigger
from buildbot.process import factory
from buildbot.config import BuilderConfig
from buildbot.scheduler import Scheduler
BuildmasterConfig = c = {}

c['slaves'] = [BuildSlave('bot1', 'sekrit')]
c['slavePortnum'] = 0
c['builders'] = []

trigger_factory = factory.BuildFactory([trigger.Trigger(schedulerNames=['downstream'])])
dummy_factory = factory.BuildFactory([dummy.Dummy(timeout=1)])
c['builders'].append(BuilderConfig('dummy1', slavename='bot1', factory=trigger_factory))
c['builders'].append(BuilderConfig('dummy2', slavename='bot1', factory=dummy_factory))

locales = {'en-US': [],
           'fr': ['linux'],
           'es': ['osx'],
           'en-GB': [],
          }

from buildbotcustom.l10n import TriggerableL10n
s1 = Scheduler('upstream', None, None, ['dummy1'])
s2 = TriggerableL10n('downstream', ['dummy2'], 'linux', locales=locales)

c['schedulers'] = [s1, s2]
c['mergeRequests'] = lambda builder, req1, req2: False
"""
class TestTriggerableL10n(MasterMixin, StallMixin, unittest.TestCase):
    def testParse(self):
        self.basedir = "l10n/triggerable/parse"
        self.create_master()
        d = self.master.loadConfig(trigger_config)
        return d

    def testRun(self):
        self.basedir = "l10n/triggerable/run"
        self.create_master()
        d = self.master.loadConfig(trigger_config)

        d.addCallback(lambda ign: self.connectSlave(['dummy1', 'dummy2']))

        def _connected(ign):
            c = Change("foo", [], "bar")
            self.master.change_svc.addChange(c)
        d.addCallback(_connected)

        d.addCallback(self.stall, 5)

        def _check(ign):
            # dummy1 should have one build
            b = self.status.getBuilder('dummy1').getLastFinishedBuild()
            self.failUnless(b)
            self.failUnlessEqual(b.getNumber(), 0)

            # dummy2 should have 2 builds (en-GB, fr, but not en-US or es)
            # en-US doesn't need repacking, and es doesn't have linux
            b = self.status.getBuilder('dummy2').getLastFinishedBuild()
            self.failUnless(b)
            self.failUnlessEqual(b.getNumber(), 1)

        d.addCallback(_check)

        return d
