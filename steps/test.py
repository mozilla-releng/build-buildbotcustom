# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
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
# The Original Code is Mozilla-specific Buildbot steps.
#
# The Initial Developer of the Original Code is
# Mozilla Corporation.
# Portions created by the Initial Developer are Copyright (C) 2007
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Ben Hearsum <bhearsum@mozilla.com>
#   Rob Campbell <rcampbell@mozilla.com>
#   Chris Cooper <coop@mozilla.com>
#   Alice Nodelman <anodelman@mozilla.com>
# ***** END LICENSE BLOCK *****

from buildbot.steps.shell import ShellCommand
from buildbot.status.builder import FAILURE, SUCCESS, WARNINGS
from buildbot.process.buildstep import BuildStep

from twisted.internet.defer import DeferredList
from twisted.python import log
from twisted.web.client import getPage

from urllib import urlencode
from time import strptime, strftime, localtime, mktime
import re
import os
import signal
from os import path
import string

class AliveTest(ShellCommand):
    name = "alive test"
    description = ["alive test"]
    haltOnFailure = True
    flunkOnFailure = False
    warnOnFailure = True

    def __init__(self, extraArgs=None, logfile=None, **kwargs):
        ShellCommand.__init__(self, **kwargs)

        self.addFactoryArguments(extraArgs=extraArgs,
                                 logfile=logfile)
        self.extraArgs = extraArgs
        self.logfile = logfile

        # build the command
        self.command = ['python', 'leaktest.py']
        if logfile:
            self.command.extend(['-l', logfile])
        if extraArgs:
            self.command.append('--')
            self.command.extend(extraArgs)


def formatBytes(bytes, sigDigits=3):
    # Force a float calculation
    bytes=float(str(bytes) + '.0')

    if bytes > 1024**3:
        formattedBytes = setSigDigits(bytes / 1024**3, sigDigits) + 'G'
    elif bytes > 1024**2:
        formattedBytes = setSigDigits(bytes / 1024**2, sigDigits) + 'M'
    elif bytes > 1024**1:
        formattedBytes = setSigDigits(bytes / 1024, sigDigits) + 'K'
    else:
        formattedBytes = setSigDigits(bytes, sigDigits)
    return str(formattedBytes) + 'B'

def formatCount(number, sigDigits=3):
    number=float(str(number) + '.0')
    return str(setSigDigits(number, sigDigits))
    
def setSigDigits(num, sigDigits=3):
    if num == 0:
        return '0'
    elif num < 10**(sigDigits-5):
        return '%.5f' % num
    elif num < 10**(sigDigits-4):
        return '%.4f' % num
    elif num < 10**(sigDigits-3):
        return '%.3f' % num
    elif num < 10**(sigDigits-2):
        return '%.2f' % num
    elif num < 10**(sigDigits-1):
        return '%.1f' % num
    return '%(num)d' % {'num': num}

def tinderboxPrint(testName,
                   testTitle,
                   numResult,
                   units,
                   printName,
                   printResult,
                   unitsSuffix=""):
    output = "TinderboxPrint:"
    output += "<abbr title=\"" + testTitle + "\">"
    output += printName + "</abbr>:"
    output += "%s\n" % str(printResult)
    output += unitsSuffix
    return output

class CompareBloatLogs(ShellCommand):
    warnOnWarnings = True
    warnOnFailure = True
    
    def __init__(self, bloatLog, testname="", testnameprefix="",
                       bloatDiffPath="tools/rb/bloatdiff.pl",
                       mozillaDir="", **kwargs):
        ShellCommand.__init__(self, **kwargs)
        self.addFactoryArguments(bloatLog=bloatLog, testname=testname,
                                 testnameprefix=testnameprefix,
                                 bloatDiffPath=bloatDiffPath)
        self.bloatLog = bloatLog
        self.testname = testname
        self.testnameprefix = testnameprefix
        self.bloatDiffPath = "build%s/%s" % (mozillaDir, bloatDiffPath)

        if len(self.testname) > 0:
            self.testname += " "
        if len(self.testnameprefix) > 0:
            self.testnameprefix += " "

        self.name = "compare " + self.testname + "bloat logs"
        self.description = ["compare " + self.testname, "bloat logs"]
        self.command = ["perl", self.bloatDiffPath, self.bloatLog + '.old',
                        self.bloatLog]

    def evaluateCommand(self, cmd):
        superResult = ShellCommand.evaluateCommand(self, cmd)
        try:
            leaks = self.getProperty('leaks')
        except:
            log.msg("Could not find build property: leaks")
            return FAILURE
        if leaks and int(leaks) > 0:
            return WARNINGS
        return superResult
            
    def createSummary(self, log):
        summary = "######################## BLOAT STATISTICS\n"
        totalLineList = []
        leaks = 0
        bloat = 0
        for line in log.readlines():
            summary += line
            if leaks == 0 and bloat == 0:
                if "TOTAL" in line:
                    m = re.search('TOTAL\s+(\d+)\s+[\-\d\.]+\%\s+(\d+)',
                                  line)
                    leaks = int(m.group(1))
                    bloat = int(m.group(2))
        summary += "######################## END BLOAT STATISTICS\n\n"

        # Scrape for leak/bloat totals from TOTAL line
        # TOTAL 23 0% 876224        
        summary += "leaks = %d\n" % leaks
        summary += "bloat = %d\n" % bloat

        leaksAbbr = "%sRLk" % self.testnameprefix
        leaksTestname = ("%srefcnt_leaks" % self.testnameprefix).replace(' ', '_')
        leaksTestnameLabel = "%srefcnt Leaks" % self.testnameprefix

        tinderLink = tinderboxPrint(leaksTestname,
                                    leaksTestnameLabel, 
                                    0,
                                    'bytes',
                                    leaksAbbr,
                                    formatBytes(leaks,3)
                                    )
        summary += tinderLink

        self.setProperty('leaks',leaks)
        self.setProperty('bloat',bloat)
        self.setProperty('testresults', [(leaksAbbr, leaksTestname, leaks, formatBytes(leaks,3))])
        self.addCompleteLog(leaksAbbr + ":" + formatBytes(leaks,3),
                            summary)

class CompareLeakLogs(ShellCommand):
    warnOnWarnings = True
    warnOnFailure = True
    leaksAllocsRe = re.compile('Leaks: (\d+) bytes, (\d+) allocations')
    heapRe = re.compile('Maximum Heap Size: (\d+) bytes')
    bytesAllocsRe = re.compile('(\d+) bytes were allocated in (\d+) allocations')

    def __init__(self, platform, mallocLog, leakFailureThreshold=7261838,
                 testname="", testnameprefix="", objdir='obj-firefox',
                 **kwargs):
        assert platform.startswith('win32') or platform.startswith('macosx') \
          or platform.startswith('linux')
        ShellCommand.__init__(self, **kwargs)
        self.addFactoryArguments(platform=platform, mallocLog=mallocLog,
                                 leakFailureThreshold=leakFailureThreshold,
                                 testname=testname,
                                 testnameprefix=testnameprefix, objdir=objdir)
        self.platform = platform
        self.mallocLog = mallocLog
        self.leakFailureThreshold = leakFailureThreshold
        self.testname = testname
        self.testnameprefix = testnameprefix
        self.objdir = objdir
        self.name = "compare " + testname + "leak logs"
        self.description = ["compare " + testname, "leak logs"]

        if len(self.testname) > 0:
            self.testname += " "
        if len(self.testnameprefix) > 0:
            self.testnameprefix += " "

        if platform.startswith("win32"):
            self.command = ['%s\\dist\\bin\\leakstats.exe' % self.objdir,
                             self.mallocLog]
        else:
            self.command = ['%s/dist/bin/leakstats' % self.objdir,
                            self.mallocLog]

    def evaluateCommand(self, cmd):
        superResult = ShellCommand.evaluateCommand(self, cmd)
        try:
            leakStats = self.getProperty('leakStats')
        except:
            log.msg("Could not find build property: leakStats")
            return FAILURE
        if leakStats and \
           leakStats['new'] and \
           leakStats['new']['leaks'] and \
           int(leakStats['new']['leaks']) > int(self.leakFailureThreshold):
            return WARNINGS
        return superResult
            
    def createSummary(self, log):
        leakStats = {}
        leakStats['old'] = {}
        leakStats['new'] = {}
        summary = self.testname + " trace-malloc bloat test: leakstats\n"

        lkAbbr = "%sLk" % self.testnameprefix
        lkTestname = ("%strace_malloc_leaks" % self.testnameprefix).replace(' ','_')
        mhAbbr = "%sMH" % self.testnameprefix
        mhTestname = ("%strace_malloc_maxheap" % self.testnameprefix).replace(' ','_')
        aAbbr  = "%sA"  % self.testnameprefix
        aTestname = ("%strace_malloc_allocs" % self.testnameprefix).replace(' ','_')

        resultSet = 'new'
        for line in log.readlines():
            summary += line
            m = self.leaksAllocsRe.search(line)
            if m:
                leakStats[resultSet]['leaks'] = m.group(1)
                leakStats[resultSet]['leakedAllocs'] = m.group(2)
                continue
            m = self.heapRe.search(line)
            if m:
                leakStats[resultSet]['mhs'] = m.group(1)
                continue
            m = self.bytesAllocsRe.search(line)
            if m:
                leakStats[resultSet]['bytes'] = m.group(1)
                leakStats[resultSet]['allocs'] = m.group(2)
                continue

        for key in ('leaks', 'leakedAllocs', 'mhs', 'bytes', 'allocs'):
            if key not in leakStats['new']:
                self.addCompleteLog('summary',
                                    'Unable to parse leakstats output')
                return

        lk =  formatBytes(leakStats['new']['leaks'],3)
        mh = formatBytes(leakStats['new']['mhs'],3)
        a =  formatCount(leakStats['new']['allocs'],3)

        self.setProperty('testresults', [ \
            (lkAbbr, lkTestname, leakStats['new']['leaks'], lk), \
            (mhAbbr, mhTestname, leakStats['new']['mhs'], mh), \
            (aAbbr, aTestname, leakStats['new']['allocs'], a)])

        self.setProperty('leakStats',leakStats)

        slug = "%s: %s, %s: %s, %s: %s" % (lkAbbr, lk, mhAbbr, mh, aAbbr, a)
        logText = ""
        if self.testname.startswith("current"):
            logText += tinderboxPrint(lkTestname,
                                      "Total Bytes malloc'ed and not free'd",
                                      0,
                                      "bytes",
                                      lkAbbr,
                                      lk)
            logText += tinderboxPrint(mhTestname,
                                      "Maximum Heap Size",
                                      0,
                                      "bytes",
                                      mhAbbr,
                                      mh)
            logText += tinderboxPrint(aTestname,
                                      "Allocations - number of calls to malloc and friends",
                                      0,
                                      "count",
                                      aAbbr,
                                      a)
        else:
            logText += "%s: %s\n%s: %s\n%s: %s\n" % (lkAbbr, lk, mhAbbr, mh, aAbbr, a)

        self.addCompleteLog(slug, logText)


class Codesighs(ShellCommand):
    def __init__(self, objdir, platform, type='auto', **kwargs):
        ShellCommand.__init__(self, **kwargs)
        self.addFactoryArguments(objdir=objdir, platform=platform, type=type)

        assert platform in ('win32', 'macosx', 'linux')
        assert type in ('auto', 'base')

        self.objdir = objdir
        self.platform = platform
        if self.platform in ('macosx', 'linux'):
            self.platform = 'unix'
        self.type = type

        runScript = 'tools/codesighs/' + \
                    type + 'summary.' + self.platform + '.bash'

        self.command = [runScript, '-o', objdir, '-s', '.',
                        '../codesize-' + type + '.log',
                        '../codesize-' + type + '-old.log',
                        '../codesize-' + type + '-diff.log']

    def createSummary(self, log):
        bytes = ""
        diff = ""
        for line in log.readlines():
            if '__codesize:' in line:
                rawBytes = line.split(':')[1].rstrip()
                bytes = formatBytes(rawBytes)
            elif '__codesizeDiff:' in line:
                diffData = line.split(':')[1].rstrip()
                # if we anything but '+0' here, we print additional data
                if diffData[0:2] != '+0':
                    diff = diffData

        z = 'Z'
        zLong = "codesighs"
        if self.type == 'base':
            z = 'mZ'
            zLong = "codesighs_embed"

        self.setProperty('testresults', [(z, zLong, rawBytes, bytes)])

        slug = '%s:%s' % (z, bytes)
        summary = 'TinderboxPrint:%s\n' % slug
        self.addCompleteLog(slug, summary)
        if diff:
            # buildbot chokes if we put all the data in the short log
            slug = '%sdiff' % z
            summary = 'TinderboxPrint:%s:%s\n' % (slug, diff)
            self.addCompleteLog(slug, summary)

class GraphServerPost(BuildStep):
    flunkOnFailure = True

    def __init__(self, server, selector, branch, resultsname, timeout=30):
        BuildStep.__init__(self)
        self.addFactoryArguments(server=server, selector=selector,
                                 branch=branch, resultsname=resultsname,
                                 timeout=timeout)
        self.server = server
        self.graphurl = "http://%s/%s/collect.cgi" % (server, selector,)
        self.branch = branch
        self.resultsname = resultsname.replace(' ', '_')
        self.timeout = timeout
        self.name = 'graph server post'

    def doTinderboxPrint(self, contents, testlongname, testname, prettyval):
        # If there was no error, process the log
        lines = contents.split('\n')
        self.stdio.addStdout(contents + '\n')
        for line in lines:
            if "RETURN:" in line :
                tboxPrint =  'TinderboxPrint: ' + \
                  '<a title = "%s" href = "http://%s/%s">%s:%s</a>\n' % \
                  (testlongname, self.server, line.rsplit(":")[3],
                   testname, prettyval) 
                self.stdio.addStdout(tboxPrint)
        
    def postFailed(self, testlongname):
        # This function is called when getPage() fails and simply sets
        # self.error = True so postFinished knows that something failed.
        self.error = True
        self.stdio.addStderr('Encountered error when trying to post %s\n' % \
          testlongname)

    def start(self):
        self.changes = self.build.allChanges()
        self.timestamp = int(self.step_status.build.getTimes()[0])
        self.buildid = strftime("%Y%m%d%H%M", localtime(self.timestamp))
        self.testresults = self.getProperty('testresults')
        self.stdio = self.addLog('stdio')
        self.error = False
        # Make a list of Deferreds so we can properly clean up once everything
        # has posted.
        deferreds = []
        for res in self.testresults:
            testname, testlongname, testval, prettyval = res
            params = urlencode({'branchid': self.buildid, 'value': str(testval).strip(string.letters), 'testname': testlongname, 'tbox' : self.resultsname, 'type' : "continuous", 'time' : self.timestamp, 'branch' : self.branch})
            d = getPage(self.graphurl, timeout=self.timeout, method='POST',
                        postdata=params)
            d.addCallback(self.doTinderboxPrint, testlongname, testname,
                          prettyval)
            d.addErrback(lambda x: self.postFailed(testlongname))
            deferreds.append(d)

        # Now, once *everything* has finished we need to tell Buildbot
        # that this step is complete.
        dl = DeferredList(deferreds)
        dl.addCallback(self.postFinished)

    def postFinished(self, results):
        if self.error:
            self.step_status.setText(["Error:", "failed", "graph", "server", "post"])
            self.step_status.setText2(["Error:", "failed", "graph", "server", "post"])
            self.finished(FAILURE)
        else:
            self.finished(SUCCESS)
