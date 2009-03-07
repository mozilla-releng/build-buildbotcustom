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

import utils
from utils import Request

__all__ = ['L10nTest', 'EnTest', 'ChangeCoalescenceTest']

config = """
import buildbotcustom.l10n
import buildbotcustom.log
buildbotcustom.log.init(scheduler = buildbotcustom.log.DEBUG,
                        dispatcher = buildbotcustom.log.DEBUG)

from buildbot.process import factory
from buildbot.steps import dummy
from buildbot.buildslave import BuildSlave
s = factory.s

f = factory.BuildFactory([
    s(dummy.Dummy, timeout=%(timeout)i),
    ])

BuildmasterConfig = c = {}
c['slaves'] = [BuildSlave('bot1', 'sekrit')]
c['schedulers'] = []

from buildbotcustom.l10n import Scheduler, L10nDispatcher, EnDispatcher

s = Scheduler("l10n", "")
c['schedulers'].append(s)

paths = ['browser', 'toolkit']
locales = ['af', 'ar']
s.addDispatcher(L10nDispatcher(paths, 'HEAD', 'fx_tree'))
s.addDispatcher(EnDispatcher(paths, 'HEAD', 'fx_tree', 'mozilla/'))
s.locales['fx_tree'] = locales
s.builders['fx_tree'] = ['dummy']
s.apps['fx_tree'] = 'browser'

c['builders'] = []
c['builders'].append({'name': 'dummy', 'slavename': 'bot1',
                      'builddir': 'dummy1', 'factory': f})
c['slavePortnum'] = 0
"""

testconfig = config

class L10nTest(utils.TimedChangesQueue):
  """Test case to verify that a single l10n check-in actually
  triggers a single l10n build.
  
  """
  config = testconfig % dict(timeout=1)
  requests = (
    Request(when = 1200000000,
            delay = 0,
            branch = 'HEAD',
            files = "l10n/af/browser/file"),
  )
  def allBuildsDone(self):
    # buildbot data tests
    #  None
    # database tests
    bs = self.status.getBuilder('dummy')
    self.assertEquals(bs.nextBuildNumber, 1)
    build = bs.getBuild(-1)
    self.assertEquals(build.getProperty('locale'), 'af')
    self.assertEquals(build.getProperty('app'), 'browser')
    self.assertEquals(build.getChanges()[0].number, 1)


class EnTest(utils.TimedChangesQueue):
  """Test case to verify that a single en-US check-in actually
  triggers a single l10n build for all locales.
  
  """
  config = testconfig % dict(timeout=1)
  requests = (
    Request(when = 1200000000,
            delay = 0,
            branch = 'HEAD',
            files = "mozilla/browser/locales/en-US/file"),
  )
  def allBuildsDone(self):
    """twisted callback running the tests"""
    # buildbot data tests
    #  None
    # database tests
    bs = self.status.getBuilder('dummy')
    self.assertEquals(bs.nextBuildNumber, 2)
    builds = [bs.getBuild(n) for n in xrange(2)]
    locales = [b.getProperty('locale') for b in builds]
    locales.sort()
    self.assertEquals(locales, ['af', 'ar'])
    self.assertEquals(builds[0].getProperty('app'), 'browser')
    self.assertEquals(map(lambda c: c.number,
                          builds[1].getChanges()),
                      [1])
    self.assertEquals(map(lambda c: c.number,
                          builds[0].getChanges()),
                      [1])

# disable test, we're not coalescing builds right now
#class ChangeCoalescenceTest(utils.TimedChangesQueue):
class __Ignore:
  '''Test case to verify that a single check-in to en-US, 
  followed by a single check-in to one locale that wasn't built
  yet actually merges the second check-in into the build queue.
  Result should be first locale built against the first change,
  and the second locale built against both the first and the
  second change, and really just two builds.
  '''
  config = testconfig % dict(timeout=2)
  requests = (
    Request(when = 1200000000,
            delay = 0,
            branch = 'HEAD',
            files = "mozilla/browser/locales/en-US/file"),
    Request(when = 1200000001,
            delay = 1,
            files = "l10n/ar/browser/file"),
  )
  def allBuildsDone(self):
    # buildbot data tests
    builder = self.status.getBuilder('dummy')
    build = builder.getBuildByNumber(0)
    self.assertEquals(len(build.getChanges()), 1)
    build = builder.getBuildByNumber(1)
    self.assertEquals(len(build.getChanges()), 2)
    self.assertEquals(Build.objects.count(), 2)
    # database tests
    builds = list(Build.objects.all())
    self.assertEquals(builds[0].getProperty('app'), 'browser')
    self.assertEquals(builds[0].getProperty('locale'), 'af')
    self.assertEquals(builds[1].getProperty('locale'), 'ar')
    self.assertEquals(list(builds[0].changes.values('id')),
                      [dict(id = 1)])
    self.assertEquals(list(builds[1].changes.values('id')),
                      [dict(id = 1), dict(id = 2)])
