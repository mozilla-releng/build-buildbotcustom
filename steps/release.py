import re

from buildbot.process.properties import WithProperties
from buildbot.status.builder import FAILURE, SUCCESS, WARNINGS, worst_status

from buildbotcustom.steps.base import ShellCommand
from buildbotcustom.steps.misc import TinderboxShellCommand


class SnippetComparison(ShellCommand):
    # Alphas/Betas are WARN'ed about in a releasetest vs release comparison
    # because they were shipped on the 'beta' channel, which means patcher
    # will not generate release channel snippets for them. This is OK, but we
    # need to ensure we don't fail the whole comparison because of it
    acceptable_warnings_regex = re.compile(
        'WARN: [\w.]+/\w+/[0-9.]+[ab][0-9]+/.*release.* exists')
    # If we find +++ at the start of a line, let's assume it's output from
    # 'diff', which is always cause for concern.
    diff_regex = re.compile('^---')

    # command and name get overridden at runtime with whatever is passed to
    # ShellCommand.__init__
    def __init__(self, chan1, chan2, dir1, dir2, name=None, command=None,
                 timeout=10 * 60, description=['snippet', 'compare'],
                 warnOnFailure=True, warnOnWarnings=True, flunkOnFailure=False,
                 **kwargs):
        self.super_class = ShellCommand
        if command is None:
            command = ['bash',
                       WithProperties('%(toolsdir)s/release/compare-channel-snippets.sh'),
                       dir1, chan1, dir2, chan2]
        if name is None:
            name = 'compare_%s_to_%s' % (chan1, chan2)
        self.super_class.__init__(self,
                                  name=name,
                                  command=command,
                                  timeout=timeout,
                                  description=description,
                                  warnOnFailure=warnOnFailure,
                                  warnOnWarnings=warnOnWarnings,
                                  flunkOnFailure=flunkOnFailure,
                                  **kwargs)
        self.addFactoryArguments(
            chan1=chan1, chan2=chan2, dir1=dir1, dir2=dir2)
        self.warnings = False

    def createSummary(self, stdio):
        unacceptable_warnings = []
        diffs = []
        lines = stdio.readlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('WARN') and \
                    not self.acceptable_warnings_regex.search(line):
                unacceptable_warnings.extend(lines[i:i + 2])
            if self.diff_regex.search(line):
                diffs.extend(lines[i:i + 2])
            i += 1

        if len(unacceptable_warnings) > 0:
            self.addCompleteLog('Warnings', "".join(unacceptable_warnings))
            self.warnings = True
        if len(diffs) > 0:
            log = "The following files have differences:\n\n"
            log += "".join(diffs)
            log += "\nFull details are in the stdio log.\n"
            self.addCompleteLog('Diffs', log)
            self.warnings = True

    def evaluateCommand(self, cmd):
        super_result = self.super_class.evaluateCommand(self, cmd)
        # If it catches something we don't, it's almost certainly worse
        if super_result not in (SUCCESS, WARNINGS):
            return super_result
        # Warnings already excludes warnings we know to be OK, so if that log
        # exists we can assume we should warn because of it
        if self.warnings:
            return WARNINGS
        # If ShellCommand didn't find anything, and we haven't found any
        # warnings, we're good!
        return SUCCESS
