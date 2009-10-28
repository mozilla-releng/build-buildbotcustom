import os.path
import time
import re
from urllib2 import urlopen, unquote

from twisted.python import log, failure
from twisted.internet import defer, reactor
from twisted.internet.task import LoopingCall

from buildbot.process.factory import BuildFactory
from buildbot.steps.shell import Compile, ShellCommand, WithProperties, \
  SetProperty
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, SKIPPED, \
  EXCEPTION
from buildbot.changes import base, changes

import buildbotcustom.steps.misc
reload(buildbotcustom.steps.misc)
from buildbotcustom.steps.misc import FindFile, DownloadFile, UnpackFile, \
  DisconnectStep

import buildbotcustom.steps.unittest
reload(buildbotcustom.steps.unittest)
from buildbotcustom.steps.unittest import ShellCommandReportTimeout, \
  emphasizeFailureText, summaryText



#-------------------------------------------------------------------------
# Unit tests
#-------------------------------------------------------------------------

# Largely built from PackagedUnittests
class MaemoUnittestFactory(BuildFactory):
    def __init__(self, activeTests=None, reboot=True):
        BuildFactory.__init__(self)
        self.binaryDir = '/builds/unittest'
        self.maemkitDir = '/tools/maemkit'
        self.baseDir = '/builds'
        self.activeTests = activeTests
        self.reboot = reboot
        self.mozChangesetLink = '<a href=%(repo_path)s/rev/%(got_revision)s title="Built from Mozilla revision %(got_revision)s">moz:%(got_revision)s</a>'
        self.mobileChangesetLink = '<a href=%(repo_path)s/rev/%(got_revision)s title="Built from Mobile revision %(got_revision)s">mobile:%(got_revision)s</a>'

        assert self.activeTests is not None

        self.addStep(ShellCommand,
            command='echo "TinderboxPrint: `hostname`"; ifconfig -a; date',
            description='hostname',
        )
        self.addStep(ShellCommand,
            command='rm -rf %s fennec* talos* pageloader* /home/user/.mozilla /root/.mozilla /media/mmc2/.mozilla' % self.binaryDir,
            workdir=self.baseDir,
            haltOnFailure=True,
        )
        self.addStep(ShellCommand,
            command=['mkdir', '-p', '%s/maemkit_logs' % self.binaryDir],
            workdir=self.baseDir
        )

        # Download the build
        def get_fileURL(build):
            fileURL = build.source.changes[-1].files[0]
            build.setProperty('fileURL', fileURL, 'DownloadFile')
            return fileURL
        self.addStep(DownloadFile,
            url_fn=get_fileURL,
            filename_property='build_filename',
            haltOnFailure=True,
            name="download build",
            timeout=3600,
            workdir=self.binaryDir
        )
        self.addStep(ShellCommand,
            command=['tar', 'xjvf', WithProperties('%(build_filename)s')],
            workdir=self.binaryDir,
            haltOnFailure=True
        )
        self.addStep(SetProperty,
            command=['cat', 'xulrunner/platform.ini'],
            workdir='%s/fennec' % self.binaryDir,
            extract_fn=self.get_build_info,
            description=['get', 'moz', 'revision'],
        )
        self.addStep(ShellCommand,
            command=['echo', 'TinderboxPrint:',
                     WithProperties(self.mozChangesetLink)]
        )

        self.addStep(ShellCommand,
            command=['sh', '/tools/maemkit/hackTestUrl.sh',
                     WithProperties('%(fileURL)s')],
            workdir=self.binaryDir,
            haltOnFailure=True,
            description=['get', 'test', 'tarball']
        )

        self.addStep(SetProperty,
            command=['cat', 'application.ini'],
            workdir='%s/fennec' % self.binaryDir,
            extract_fn=self.get_build_info,
            description=['get', 'mobile', 'revision'],
        )
        self.addStep(ShellCommand,
            command=['echo', 'TinderboxPrint:',
                     WithProperties(self.mobileChangesetLink)]
        )

        self.addStep(ShellCommand,
            command='echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor',
            description=['disable', 'scaling', 'governor'],
            haltOnFailure=True,
        )
        self.addStep(ShellCommand,
            command=['cp', 'reftest/reftest/chrome/reftest.jar',
                     'fennec/chrome/'],
            workdir=self.binaryDir,
            description=['copy', 'reftest.jar'],
        )
        for testName in self.activeTests.keys():
            testCmd = ['python', 'maemkit-chunked.py']

            logName = 'log_%s.txt' % testName
            if 'testType' in self.activeTests[testName]:
                testCmd.append('--testtype=%s' %
                               self.activeTests[testName]['testType'])
                logName = 'log_%s.txt' % self.activeTests[testName]['testType']
            else:
                testCmd.append('--testtype=%s' % testName)

            if ('totalClients' in self.activeTests[testName] and
                    'clientNumber' in self.activeTests[testName]):
                testCmd.append('--client-number=%d' %
                               self.activeTests[testName]['clientNumber'])
                testCmd.append('--total-clients=%d' %
                               self.activeTests[testName]['totalClients'])

            knownFailCount = activeTests[testName].get('knownFailCount', 0)

            # MobileParseTestLog seems to have issues with the raw output
            # maemkit -- either that or I had crashing issues here.
            # Bumped MobileParseTestLog to the next step.
            self.addStep(ShellCommand,
                command=testCmd,
                workdir=self.maemkitDir,
                description=testName,
                timeout=60*60,
                haltOnFailure=False,
            )
            self.addStep(MobileParseTestLog,
                name=testName,
                command=['cat', logName],
                knownFailCount=knownFailCount,
                workdir=self.binaryDir,
                description=['parse', testName, 'log'],
                timeout=120,
                flunkOnFailure=False,
                haltOnFailure=False,
            )
        if self.reboot:
            self.addStep(DisconnectStep,
                command="reboot; sleep 600",
                force_disconnect=True,
                warnOnFailure=False,
                flunkOnFailure=False,
                alwaysRun=True
            )

    # from UnittestPackagedBuildFactory
    def get_build_info(self, rc, stdout, stderr):
        retval = {}
        stdout = "\n".join([stdout, stderr])
        m = re.search("^BuildID=(\w+)", stdout, re.M)
        if m:
            retval['buildid'] = m.group(1)
        m = re.search("^SourceStamp=(\w+)", stdout, re.M)
        if m:
            retval['got_revision'] = m.group(1)
        m = re.search("^SourceRepository=(\S+)", stdout, re.M)
        if m:
            retval['repo_path'] = m.group(1)
        return retval

# Wasn't able to get ShellCommandReportTimeout working; may try again
# later.
class MobileParseTestLog(ShellCommand):
    warnOnFailure = True
    warnOnWarnings = True

    def __init__(self, name=None, command=None, knownFailCount=0,
                 timeout=60, **kwargs):
        self.super_class = ShellCommand
        self.name = name
        self.knownFailCount = knownFailCount

        if not command:
            command = ['python', 'maemkit-chunked.py',
                          '--testtype=%s' % name],

        ShellCommand.__init__(self, timeout=timeout, command=command, **kwargs)

        self.addFactoryArguments(command=command, timeout=timeout,
                                 knownFailCount=knownFailCount)

    def createSummary(self, log):
        summary = ""
        crashed = leaked = False
        passCount = knownCount = failCount = 0

        skipIdent = "EXPECTED RANDOM"
        passIdent = "TEST-PASS"
        failIdent = "TEST-UNEXPECTED-"
        knownIdent = "TEST-KNOWN-FAIL"

        harnessErrorsRe = re.compile(r"TEST-UNEXPECTED-FAIL \| .* \| (Browser crashed \(minidump found\)|missing output line for total leaks!|negative leaks caught!|leaked \d+ bytes during test execution)")

        for line in log.readlines():
            m = harnessErrorsRe.match(line)
            if m:
                r = m.group(1)
                if r == "Browser crashed (minidump found)":
                    crashed = True
                elif r == "missing output line for total leaks!":
                    leaked = None
                else:
                    leaked = True
            if skipIdent in line:
                continue
            if passIdent in line:
                passCount = passCount + 1
                continue
            if failIdent in line:
                failCount = failCount + 1
                continue
            if knownIdent in line:
                knownCount = knownCount + 1

        if (failCount):
            summary = "Orig fail count: %d\nOrig known count: %d\n" % (
                    failCount, knownCount)
            if failCount > self.knownFailCount:
                failCount = failCount - self.knownFailCount
                knownCount = knownCount + self.knownFailCount
            else:
                knownCount = knownCount + failCount
                failCount = 0

        # Add the summary.
        if (passCount > 0):
            summary = "%sTinderboxPrint: %s<br/>%s [%d]\n" % (summary,
                    self.name, summaryText(passCount, failCount,
                                           knownCount, crashed=crashed,
                                           leaked=leaked),
                    self.knownFailCount)
        else:
            summary = emphasizeFailureText("T-FAIL")

        self.addCompleteLog('summary', summary)

    def evaluateCommand(self, cmd):
        superResult = self.super_class.evaluateCommand(self, cmd)
        if SUCCESS != superResult:
            return superResult

        cmdText = cmd.logs['stdio'].getText()
        if cmdText != str(cmdText):
            return WARNINGS

        m = re.findall('TEST-UNEXPECTED-', cmdText)
        if len(m) > self.knownFailCount:
            return WARNINGS
        if re.search('FAIL Exited', cmdText):
            return WARNINGS

        if self.name.startswith('mochitest') or self.name.startswith('chrome'):
            # Support browser-chrome result summary format which differs
            # from MozillaMochitest's.
            if self.name != 'mochitest-browser-chrome':
                if not re.search('TEST-PASS', cmdText):
                    return WARNINGS

        return SUCCESS



class MaemoRunPerfTests(ShellCommand):
    """Run the performance tests"""
       
    def __init__(self, hackTbPrint=None,
                 branch=None, configFile=None, command=None, **kwargs):
        self.branch = branch
        self.configFile = configFile
        if not command:
            command = ["python", "run_tests.py"]
        ShellCommand.__init__(self, command=command, **kwargs)
        self.hackTbPrint = hackTbPrint
    
    def createSummary(self, log):
        summary = []
        for line in log.readlines():
            if "RETURN:" in line:
                # The RETURN: messages aren't designed to have multiple
                # test runs; hacking them to be less noisy
                if (self.hackTbPrint):
                    line.replace("<br>", "")
                    if "graph.html" in line:
                        if "| " in line:
                            p = re.compile(r'\s*\|\s*')
                            line = 'RETURN:<small>%s</small>' % \
                                   p.split(line)[1]
                    else:
                        continue
                summary.append(line.replace("RETURN:", "TinderboxPrint:"))
            if "FAIL:" in line:
                summary.append(line.replace("FAIL:", "TinderboxPrint:FAIL:"))
        self.addCompleteLog('summary', "\n".join(summary))
            
    def evaluateCommand(self, cmd):
        superResult = ShellCommand.evaluateCommand(self, cmd)
        stdioText = cmd.logs['stdio'].getText()
        if SUCCESS != superResult:
            return FAILURE
        if None != re.search('ERROR', stdioText):
            return FAILURE
        if None != re.search('USAGE:', stdioText):
            return FAILURE
        if None != re.search('FAIL:', stdioText):
            return WARNINGS
        return SUCCESS

#
# Offshoot of TalosFactory, without any of the checkouts and with a
# number of options to make running on the mobile devices possible.
#
class MaemoTalosFactory(BuildFactory):
    """Create maemo talos build factory"""
    
    maemoClean = "rm -rf fennec fennec*.tar.bz2 talos* pageloader* /home/user/.mozilla /root/.mozilla unittest /media/mmc2/.mozilla"
    mozChangesetLink = '<a href=%(repo_path)s/rev/%(got_revision)s title="Built from Mozilla revision %(got_revision)s">moz:%(got_revision)s</a>'
    mobileChangesetLink = '<a href=%(repo_path)s/rev/%(got_revision)s title="Built from Mobile revision %(got_revision)s">mobile:%(got_revision)s</a>'

    def __init__(self, talosConfigFile='sample.config', resultsServer=None,
                 builderName='unnamed', hackTbPrint=0, reboot=True,
                 branch=None, baseDir='/builds',
                 activeTests={
                     'ts':       60,
                     'tp4':      90,
                     'tdhtml':   60,
                     'tsvg':     60,
                     'twinopen': 60,
                     'tsspider': 60,
                     'tgfx':     60,
                 },
                 talosTarball='http://mobile-master.mv.mozilla.com/maemo/talos.tar.bz2',
                 pageloaderTarball='http://mobile-master.mv.mozilla.com/maemo/pageloader.tar.bz2',
                 nochrome=False
    ):
        BuildFactory.__init__(self)
        self.baseDir = baseDir
        self.branch = branch
        self.hackTbPrint = hackTbPrint
        self.reboot = reboot
        self.talosTarball = talosTarball
        self.pageloaderTarball = pageloaderTarball
        if nochrome:
            nochrome = '--noChrome'
        else:
            nochrome = ''

        self.addStep(ShellCommand,
            command='echo "TinderboxPrint: `hostname`"; ifconfig -a; date',
            description="hostname",
        )
        self.addStep(ShellCommand,
            workdir=self.baseDir,
            description="Cleanup",
            command=self.maemoClean,
            haltOnFailure=True
        )
        self.addDownloadBuildStep()
        self.addUnpackBuildSteps()
        self.addStep(SetProperty,
            command=['cat', 'xulrunner/platform.ini'],
            workdir='%s/fennec' % self.baseDir,
            extract_fn=self.get_build_info,
            description=['get', 'moz', 'revision'],
        )
        self.addStep(ShellCommand,
            command=['echo', 'TinderboxPrint:',
                     WithProperties(self.mozChangesetLink)]
        )
        self.addStep(SetProperty,
            command=['cat', 'application.ini'],
            workdir='%s/fennec' % self.baseDir,
            extract_fn=self.get_build_info,
            description=['get', 'mobile', 'revision'],
        )
        self.addStep(ShellCommand,
            command=['echo', 'TinderboxPrint:',
                     WithProperties(self.mobileChangesetLink)]
        )

        self.addStep(ShellCommand,
            command=['wget', self.talosTarball, '-O', 'talos.tar.bz2'],
            workdir=self.baseDir,
            haltOnFailure=True,
            timeout=60*60,
            name="Download talos",
        )
        self.addStep(UnpackFile(
            filename='talos.tar.bz2',
            workdir=self.baseDir,
            haltOnFailure=True,
            timeout=60*60,
            name="Unpack talos",
        ))
        self.addStep(ShellCommand,
            command=['wget', self.pageloaderTarball, '-O', 'pageloader.tar.bz2'],
            workdir=self.baseDir,
            haltOnFailure=True,
            timeout=60*60,
            name="Download pageloader",
        )
        self.addStep(UnpackFile(
            filename='../pageloader.tar.bz2',
            workdir='%s/fennec' % self.baseDir,
            haltOnFailure=True,
            timeout=60*60,
            name="Unpack pageloader",
        ))
        self.addStep(ShellCommand,
            command=['ln', '-s', '/tools/tp4', '.'],
            workdir='%s/talos/page_load_test' % self.baseDir,
            description=['create', 'tp4', 'softlink'],
        )
        self.addStep(ShellCommand,
            command="echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
            description=['disable', 'scaling', 'governor'],
            haltOnFailure=True,
        )

        for testName in activeTests.keys():
            self.addStep(ShellCommand,
                command='python PerfConfigurator.py -v -e %s/fennec/fennec -t `hostname` --branch %s --branchName %s --activeTests %s --sampleConfig %s --browserWait %d %s --resultsServer %s --resultsLink /server/collect.cgi --output local.config' % (self.baseDir,self.branch,self.branch,testName,talosConfigFile,activeTests[testName],nochrome,resultsServer),
                workdir = "/builds/talos",
                description="Create config %s" % (testName),
                haltOnFailure=True,
            )
            self.addStep(ShellCommand(
                command=["cat", "local.config"],
                workdir = "/builds/talos",
                description="Show config %s" % (testName),
            ))
            self.addStep(ShellCommand(
                command="free; df -h",
                workdir = ".",
                description="Check memory/disk",
            ))
            self.addStep(MaemoRunPerfTests,
                command=["python", "run_tests.py", "--noisy",
                         "--debug", "local.config"],
                workdir = "/builds/talos",
                description="Run %s" % (testName),
                branch=self.branch,
                configFile="local.config",
                timeout=60 * 60,
                warnOnWarnings=True,
                haltOnFailure=False,
            )
        if self.reboot:
            self.addStep(DisconnectStep(
                command="reboot; sleep 600",
                force_disconnect=True,
                warnOnFailure=False,
                flunkOnFailure=False,
                alwaysRun=True
            ))

    # from UnittestPackagedBuildFactory
    def get_build_info(self, rc, stdout, stderr):
        retval = {}
        stdout = "\n".join([stdout, stderr])
        m = re.search("^BuildID=(\w+)", stdout, re.M)
        if m:
            retval['buildid'] = m.group(1)
        m = re.search("^SourceStamp=(\w+)", stdout, re.M)
        if m:
            retval['got_revision'] = m.group(1)
        m = re.search("^SourceRepository=(\S+)", stdout, re.M)
        if m:
            retval['repo_path'] = m.group(1)
        return retval

    # from TalosFactory
    def addDownloadBuildStep(self):
        def get_url(build):
            url = build.source.changes[-1].files[0]
            return url
        self.addStep(DownloadFile(
            url_fn=get_url,
            url_property="fileURL",
            filename_property="filename",
            workdir=self.baseDir,
            haltOnFailure=True,
            timeout=60*60,
            name="Download build",
        ))

    # also from TalosFactory. Moving towards refactoring, which is good,
    # but not there yet.
    def addUnpackBuildSteps(self):
        self.addStep(UnpackFile(
            filename=WithProperties("%(filename)s"),
            workdir=self.baseDir,
            haltOnFailure=True,
            timeout=60*60,
            name="Unpack build",
        ))

class RebootFactory(BuildFactory):
    def __init__(self):
        BuildFactory.__init__(self)
        self.addStep(ShellCommand,
            command="hostname; ifconfig -a",
            description="hostname",
        )
        self.addStep(DisconnectStep,
            command=['/etc/cron.daily/daily_reboot.sh'],
            description=['daily', 'reboot'],
            force_disconnect=True,
            warnOnFailure=False,
            haltOnFailure=False,
        )
