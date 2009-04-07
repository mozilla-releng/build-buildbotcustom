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
#   Rob Campbell <rcampbell@mozilla.com>
#   Chris Cooper <ccooper@mozilla.com>
#   Ben Hearsum <bhearsum@mozilla.com>
# ***** END LICENSE BLOCK *****

import re
import os

from buildbot.steps.shell import ShellCommand
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, SKIPPED, EXCEPTION, HEADER

cvsCoLog = "cvsco.log"
tboxClobberCvsCoLog = "tbox-CLOBBER-cvsco.log"
buildbotClobberCvsCoLog = "buildbot-CLOBBER-cvsco.log"

def emphasizeFailureText(text):
    return '<em class="testfail">%s</em>' % text

# Some test suites (like TUnit) may not (yet) have the knownFailCount feature.
# Some test suites (like TUnit) may not (yet) have the crashed feature.
# Expected values for leaked: False, no leak; True, leaked; None, report failure.
def summaryText(passCount, failCount, knownFailCount = None,
        crashed = False, leaked = False):
    # Format the tests counts.
    if passCount < 0 or failCount < 0 or \
            (knownFailCount != None and knownFailCount < 0):
        # Explicit failure case.
        summary = emphasizeFailureText("T-FAIL")
    elif passCount == 0 and failCount == 0 and \
            (knownFailCount == None or knownFailCount == 0):
        # Implicit failure case.
        summary = emphasizeFailureText("T-FAIL")
    else:
        # Handle failCount.
        failCountStr = str(failCount)
        if failCount > 0:
            failCountStr = emphasizeFailureText(failCountStr)
        # Format the counts.
        summary = "%d/%s" % (passCount, failCountStr)
        if knownFailCount != None:
            summary += "/%d" % knownFailCount

    # Format the crash status.
    if crashed:
        summary += "&nbsp;%s" % emphasizeFailureText("CRASH")

    # Format the leak status.
    if leaked != False:
        summary += "&nbsp;%s" % emphasizeFailureText((leaked and "LEAK") or "L-FAIL")

    return summary

class ShellCommandReportTimeout(ShellCommand):
    """We subclass ShellCommand so that we can bubble up the timeout errors
    to tinderbox that normally only get appended to the buildbot slave logs.
    """
    def __init__(self, **kwargs):
	self.my_shellcommand = ShellCommand
	ShellCommand.__init__(self, **kwargs)

    def evaluateCommand(self, cmd):
        superResult = self.my_shellcommand.evaluateCommand(self, cmd)
        for line in cmd.logs['stdio'].readlines(channel=HEADER):
            if "command timed out" in line:
                self.addCompleteLog('timeout',
                                    'buildbot.slave.commands.TimeoutError: ' +
                                    line +
                                    "TinderboxPrint: " + self.name + "<br/>" +
                                    emphasizeFailureText("timeout") + "\n")
                # We don't need to print a second error if we timed out
                return WARNINGS

        if cmd.rc != 0:
            self.addCompleteLog('error',
              'Unknown Error: command finished with exit code: %d' % cmd.rc)
            return WARNINGS

        return superResult

class MozillaCheckoutClientMk(ShellCommandReportTimeout):
    haltOnFailure = True
    cvsroot = ":pserver:anonymous@cvs-mirror.mozilla.org:/cvsroot"
    
    def __init__(self, **kwargs):
        if 'cvsroot' in kwargs:
            self.cvsroot = kwargs['cvsroot']
        if not 'command' in kwargs:
            kwargs['command'] = ["cvs", "-d", self.cvsroot, "co", "mozilla/client.mk"]
        ShellCommandReportTimeout.__init__(self, **kwargs)
    
    def describe(self, done=False):
        return ["client.mk update"]
    
 
class MozillaClientMkPull(ShellCommandReportTimeout):
    haltOnFailure = True
    def __init__(self, **kwargs):
        if not 'project' in kwargs or kwargs['project'] is None:
            self.project = "browser"
        else:
            self.project = kwargs['project']
            del kwargs['project']
        if not 'workdir' in kwargs:
            kwargs['workdir'] = "mozilla"
        if not 'command' in kwargs:
            kwargs['command'] = ["make", "-f", "client.mk", "pull_all"]
        # MOZ_CO_PROJECT: "used in the try server cvs trunk builders,
        #   not used by the unittests on tryserver though".
        if not "env" in kwargs:
            kwargs["env"] = {}
        kwargs["env"]["MOZ_CO_PROJECT"] = self.project
        ShellCommandReportTimeout.__init__(self, **kwargs)
    
    def describe(self, done=False):
        if not done:
            return ["pulling (" + self.project + ")"]
        return ["pull (" + self.project + ")"]
    

class MozillaPackage(ShellCommandReportTimeout):
    name = "package"
    warnOnFailure = True
    description = ["packaging"]
    descriptionDone = ["package"]
    command = ["make"]

class UpdateClobberFiles(ShellCommandReportTimeout):
    name = "update clobber files"
    warnOnFailure = True
    description = "updating clobber files"
    descriptionDone = "clobber files updated"
    clobberFilePath = "clobber_files/"
    logDir = '../logs/'

    def __init__(self, **kwargs):
        if not 'platform' in kwargs:
            return FAILURE
        self.platform = kwargs['platform']
        if 'clobberFilePath' in kwargs:
            self.clobberFilePath = kwargs['clobberFilePath']
        if 'logDir' in kwargs:
            self.logDir = kwargs['logDir']
        if self.platform.startswith('win'):
            self.tboxClobberModule = 'mozilla/tools/tinderbox-configs/firefox/win32'
        else:
            self.tboxClobberModule = 'mozilla/tools/tinderbox-configs/firefox/' + self.platform
        if 'cvsroot' in kwargs:
            self.cvsroot = kwargs['cvsroot']
        if 'branch' in kwargs:
            self.branchString = ' -r ' + kwargs['branch']
            self.buildbotClobberModule = 'mozilla/tools/buildbot-configs/testing/unittest/CLOBBER/firefox/' + kwargs['branch'] + '/' + self.platform
        else:
            self.branchString = ''
            self.buildbotClobberModule = 'mozilla/tools/buildbot-configs/testing/unittest/CLOBBER/firefox/TRUNK/' + self.platform 
            
        if not 'command' in kwargs:
            self.command = r'cd ' + self.clobberFilePath + r' && cvs -d ' + self.cvsroot + r' checkout' + self.branchString + r' -d tinderbox-configs ' + self.tboxClobberModule + r'>' + self.logDir + tboxClobberCvsCoLog + r' && cvs -d ' + self.cvsroot + r' checkout -d buildbot-configs ' + self.buildbotClobberModule + r'>' + self.logDir + buildbotClobberCvsCoLog
        ShellCommandReportTimeout.__init__(self, **kwargs)

class MozillaClobber(ShellCommandReportTimeout):
    name = "clobber"
    description = "checking clobber file"
    descriptionDone = "clobber checked"
    clobberFilePath = "clobber_files/"
    logDir = 'logs/'
    
    def __init__(self, **kwargs):
        if 'platform' in kwargs:
            self.platform = kwargs['platform']
        if 'logDir' in kwargs:
            self.logDir = kwargs['logDir']
        if 'clobberFilePath' in kwargs:
            self.clobberFilePath = kwargs['clobberFilePath']
        if not 'command' in kwargs:
            tboxGrepCommand = r"grep -q '^U tinderbox-configs.CLOBBER' " + self.logDir + tboxClobberCvsCoLog
            tboxPrintHeader = "echo Tinderbox clobber file updated"
            tboxCatCommand = "cat %s/tinderbox-configs/CLOBBER" % self.clobberFilePath
            buildbotGrepCommand = r"grep -q '^U buildbot-configs.CLOBBER' " + self.logDir + buildbotClobberCvsCoLog
            buildbotPrintHeader = "echo Buildbot clobber file updated"
            buildbotCatCommand = "cat %s/buildbot-configs/CLOBBER" % self.clobberFilePath
            rmCommand = "rm -rf mozilla"
            printExitStatus = "echo No clobber required"
            self.command = tboxGrepCommand + r' && ' + tboxPrintHeader + r' && ' + tboxCatCommand + r' && ' + rmCommand + r'; if [ $? -gt 0 ]; then ' + buildbotGrepCommand + r' && ' + buildbotPrintHeader + r' && ' + buildbotCatCommand + r' && ' + rmCommand + r'; fi; if [ $? -gt 0 ]; then ' + printExitStatus + r'; fi'
        ShellCommandReportTimeout.__init__(self, **kwargs)

class MozillaClobberWin(ShellCommandReportTimeout):
    name = "clobber win"
    description = "checking clobber file"
    descriptionDone = "clobber finished"
    
    def __init__(self, **kwargs):
        platformFlag = ""
        slaveNameFlag = ""
        branchFlag = ""
        if 'platform' in kwargs:
            platformFlag = " --platform=" + kwargs['platform']
        if 'slaveName' in kwargs:
            slaveNameFlag = " --slaveName=" + kwargs['slaveName']
        if 'branch' in kwargs:
            branchFlag = " --branch=" + kwargs['branch']
        if not 'command' in kwargs:
            self.command = 'python C:\\Utilities\\killAndClobberWin.py' + platformFlag + slaveNameFlag + branchFlag
        ShellCommandReportTimeout.__init__(self, **kwargs)

class MozillaCheck(ShellCommandReportTimeout):
    warnOnFailure = True

    # test_name defaults to "check" until all the callers are converted.
    def __init__(self, test_name="check", **kwargs):
        self.name = test_name
        if test_name == "check":
            # Target executing recursively in all (sub)directories.
            self.command = ["make", "-k", test_name]
        else:
            # Target calling a python script.
            self.command = ["make", test_name]
        self.description = [test_name + " test"]
        self.descriptionDone = [self.description[0] + " complete"]
	self.super_class = ShellCommandReportTimeout
	ShellCommandReportTimeout.__init__(self, **kwargs)
   
    def createSummary(self, log):
        # Counts.
        passCount = 0
        failCount = 0
        leaked = False

        # Regular expression for crash and leak detections.
        harnessErrorsRe = re.compile(r"TEST-UNEXPECTED-FAIL \| .* \| (missing output line for total leaks!|negative leaks caught!|leaked \d+ bytes during test execution)")
        # Process the log.
        for line in log.readlines():
            if "TEST-PASS" in line:
                passCount = passCount + 1
                continue
            if "TEST-UNEXPECTED-" in line:
                # Set the error flags.
                # Or set the failure count.
                m = harnessErrorsRe.match(line)
                if m:
                    r = m.group(1)
                    if r == "missing output line for total leaks!":
                        leaked = None
                    else:
                        leaked = True
                else:
                    failCount = failCount + 1
                # continue

        # Add the summary.
        summary = "TinderboxPrint: %s<br/>%s\n" % (self.name,
            summaryText(passCount, failCount, leaked = leaked))
        self.addCompleteLog('summary', summary)
    
    def evaluateCommand(self, cmd):
        superResult = self.super_class.evaluateCommand(self, cmd)
        if SUCCESS != superResult:
            return WARNINGS
        if None != re.search('TEST-UNEXPECTED-', cmd.logs['stdio'].getText()):
            return WARNINGS
        return SUCCESS
    
class MozillaReftest(ShellCommandReportTimeout):
    warnOnFailure = True

    def __init__(self, test_name, **kwargs):
        self.name = test_name
        self.command = ["make", test_name]
        self.description = [test_name + " test"]
        self.descriptionDone = [self.description[0] + " complete"]
	self.super_class = ShellCommandReportTimeout
	ShellCommandReportTimeout.__init__(self, **kwargs)
   
    def createSummary(self, log):
        # Counts.
        successfulCount = -1
        unexpectedCount = -1
        knownProblemsCount = -1
        crashed = False
        leaked = False

        # Regular expression for result summary details.
        infoRe = re.compile(r"REFTEST INFO \| (Successful|Unexpected|Known problems): (\d+) \(")
        # Regular expression for crash and leak detections.
        harnessErrorsRe = re.compile(r"TEST-UNEXPECTED-FAIL \| .* \| (Browser crashed \(minidump found\)|missing output line for total leaks!|negative leaks caught!|leaked \d+ bytes during test execution)")
        # Process the log.
        for line in log.readlines():
            # Set the counts.
            m = infoRe.match(line)
            if m:
                r = m.group(1)
                if r == "Successful":
                    successfulCount = int(m.group(2))
                elif r == "Unexpected":
                    unexpectedCount = int(m.group(2))
                elif r == "Known problems":
                    knownProblemsCount = int(m.group(2))
                continue
            # Set the error flags.
            m = harnessErrorsRe.match(line)
            if m:
                r = m.group(1)
                if r == "Browser crashed (minidump found)":
                    crashed = True
                elif r == "missing output line for total leaks!":
                    leaked = None
                else:
                    leaked = True
                # continue

        # Add the summary.
        summary = "TinderboxPrint: %s<br/>%s\n" % (self.name,
            summaryText(successfulCount, unexpectedCount, knownProblemsCount, crashed, leaked))
        self.addCompleteLog('summary', summary)

    def evaluateCommand(self, cmd):
        superResult = self.super_class.evaluateCommand(self, cmd)

        # Assume that having the "Unexpected: 0" line means the tests run completed.
        # Also check for "^TEST-UNEXPECTED-" for harness errors.
        if superResult != SUCCESS or \
                not re.search(r"^REFTEST INFO \| Unexpected: 0 \(", cmd.logs["stdio"].getText(), re.MULTILINE) or \
                re.search(r"^TEST-UNEXPECTED-", cmd.logs["stdio"].getText(), re.MULTILINE):
            return WARNINGS

        return SUCCESS

class MozillaMochitest(ShellCommandReportTimeout):
    warnOnFailure = True

    def __init__(self, test_name, leakThreshold=None, **kwargs):
        self.name = test_name
        self.command = ["make", test_name]
        self.description = [test_name + " test"]
        self.descriptionDone = [self.description[0] + " complete"]
        if leakThreshold:
            if not "env" in kwargs:
                kwargs["env"] = {}
            kwargs["env"]["EXTRA_TEST_ARGS"] = \
                "--leak-threshold=%d" % leakThreshold
        ShellCommandReportTimeout.__init__(self, **kwargs)    
	self.super_class = ShellCommandReportTimeout
    
    def createSummary(self, log):
        # Counts.
        passCount = 0
        failCount = 0
        todoCount = 0
        crashed = False
        leaked = False

        passIdent = "INFO Passed:"
        failIdent = "INFO Failed:"
        todoIdent = "INFO Todo:"
        # Support browser-chrome result summary format which differs from MozillaMochitest's.
        if self.name == 'mochitest-browser-chrome':
            passIdent = "Pass:"
            failIdent = "Fail:"
            todoIdent = "Todo:"
        # Regular expression for crash and leak detections.
        harnessErrorsRe = re.compile(r"TEST-UNEXPECTED-FAIL \| .* \| (Browser crashed \(minidump found\)|missing output line for total leaks!|negative leaks caught!|leaked \d+ bytes during test execution)")
        # Process the log.
        for line in log.readlines():
            if passIdent in line:
                passCount = int(line.split()[-1])
                continue
            if failIdent in line:
                failCount = int(line.split()[-1])
                continue
            if todoIdent in line:
                todoCount = int(line.split()[-1])
                continue
            # Set the error flags.
            m = harnessErrorsRe.match(line)
            if m:
                r = m.group(1)
                if r == "Browser crashed (minidump found)":
                    crashed = True
                elif r == "missing output line for total leaks!":
                    leaked = None
                else:
                    leaked = True
                # continue

        # Add the summary.
        summary = "TinderboxPrint: %s<br/>%s\n" % (self.name,
            summaryText(passCount, failCount, todoCount, crashed, leaked))
        self.addCompleteLog('summary', summary)

    def evaluateCommand(self, cmd):
        superResult = self.super_class.evaluateCommand(self, cmd)

        if SUCCESS != superResult:
            return WARNINGS
        if re.search('TEST-UNEXPECTED-', cmd.logs['stdio'].getText()):
            return WARNINGS
        if re.search('FAIL Exited', cmd.logs['stdio'].getText()):
            return WARNINGS
        # Support browser-chrome result summary format which differs from MozillaMochitest's.
        if self.name != 'mochitest-browser-chrome':
            if not re.search('TEST-PASS', cmd.logs['stdio'].getText()):
                return WARNINGS

        return SUCCESS

class CreateProfile(ShellCommandReportTimeout):
    name = "create profile"
    warnOnFailure = True
    description = ["create profile"]
    descriptionDone = ["create profile complete"]
    command = r'python mozilla/testing/tools/profiles/createTestingProfile.py --binary mozilla/objdir/dist/bin/firefox'

class CreateProfileWin(CreateProfile):
    command = r'python mozilla\testing\tools\profiles\createTestingProfile.py --binary mozilla\objdir\dist\bin\firefox.exe'
