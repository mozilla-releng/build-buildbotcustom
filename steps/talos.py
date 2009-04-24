import re, os, time, copy
from buildbot.steps.shell import ShellCommand, WithProperties
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, SKIPPED, EXCEPTION

class MozillaUpdateConfig(ShellCommand):
    """Configure YAML file for run_tests.py"""
    name = "Update config"

    def __init__(self, branch, branchName, executablePath, addOptions=None,
            useSymbols=False, **kwargs):

        if addOptions is None:
            self.addOptions = []
        else:
            self.addOptions = addOptions

        self.branch = branch
        self.branchName = branchName
        self.exePath = executablePath
        self.useSymbols = useSymbols

        ShellCommand.__init__(self, **kwargs)

        self.addFactoryArguments(branch=branch, addOptions=addOptions,
                branchName=branchName, executablePath=executablePath,
                useSymbols=useSymbols)

    def setBuild(self, build):
        ShellCommand.setBuild(self, build)
        title = build.slavename
        buildid = time.strftime("%Y%m%d%H%M", time.localtime(build.source.changes[-1].when))

        extraopts = copy.copy(self.addOptions)
        if self.useSymbols:
            extraopts += ['--symbolsPath', '../symbols']

        self.setCommand(["python", "PerfConfigurator.py", "-v", "-e",
            self.exePath, "-t", title, "-b", self.branch, "-d",
            buildid, '--branchName', self.branchName] + extraopts)

    def evaluateCommand(self, cmd):
        superResult = ShellCommand.evaluateCommand(self, cmd)
        if SUCCESS != superResult:
            return FAILURE
        stdioText = cmd.logs['stdio'].getText()
        if None != re.search('ERROR', stdioText):
            return FAILURE
        if None != re.search('USAGE:', stdioText):
            return FAILURE
        configFileMatch = re.search('outputName\s*=\s*(\w*?.yml)', stdioText)
        if not configFileMatch:
            return FAILURE
        else:
            self.setProperty("configFile", configFileMatch.group(1))
        return SUCCESS

class MozillaRunPerfTests(ShellCommand):
    """Run the performance tests"""
    name = "Run performance tests"

    def createSummary(self, log):
        summary = []
        for line in log.readlines():
            if "RETURN:" in line:
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
