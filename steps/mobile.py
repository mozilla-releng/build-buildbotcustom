from buildbot.steps.shell import ShellCommand
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, SKIPPED, \
    EXCEPTION


# Wasn't able to get ShellCommandReportTimeout working; may try again
# later.
class MobileParseTestLog(ShellCommand):
    warnOnFailure = True
    warnOnWarnings = True

    def __init__(self, log, known_fail_count=0,
                 timeout=60, **kwargs):
        self.super_class = ShellCommand
        self.log = log
        self.known_fail_count = known_fail_count
        command = ['cat', self.log]

        ShellCommand.__init__(self, timeout=timeout, command=command, **kwargs)

        self.addFactoryArguments(command=command, timeout=timeout,
                                 known_fail_count=known_fail_count)

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
            if failCount > self.known_fail_count:
                failCount = failCount - self.known_fail_count
                knownCount = knownCount + self.known_fail_count
            else:
                knownCount = knownCount + failCount
                failCount = 0

        # Add the summary.
        if (passCount > 0):
            summary = "%sTinderboxPrint: %s<br/>%s [%d]\n" % (summary,
                    self.name, summaryText(passCount, failCount,
                                           knownCount, crashed=crashed,
                                           leaked=leaked),
                    self.known_fail_count)
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
        if len(m) > self.known_fail_count:
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
