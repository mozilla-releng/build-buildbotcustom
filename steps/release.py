import re

from buildbot.process.properties import WithProperties
from buildbot.status.builder import FAILURE, SUCCESS, WARNINGS, worst_status

from buildbotcustom.steps.base import ShellCommand
from buildbotcustom.steps.misc import TinderboxShellCommand

class UpdateVerify(ShellCommand):
    def __init__(self, **kwargs):
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)

    def evaluateCommand(self, cmd):
        worst = self.super_class.evaluateCommand(self, cmd)
        for line in cmd.logs['stdio'].getText().split("\n"):
            if line.startswith('FAIL'):
                worst = worst_status(worst, FAILURE)
        return worst

class L10nVerifyMetaDiff(TinderboxShellCommand):
    """Run the l10n verification script.
    """
    name='l10n metadiff'
    description=['create', 'metadiff']
    descriptionDone=['created', 'metadiff']

    def __init__(self, 
                 currentProduct=None, 
                 previousProduct=None,
                 **kwargs):
        self.super_class = TinderboxShellCommand
        kwargs['ignoreCodes'] = [0,1]
        kwargs['log_eval_func'] = lambda x,y: SUCCESS
        self.super_class.__init__(self, **kwargs)
        self.addFactoryArguments(currentProduct=currentProduct,
                                 previousProduct=previousProduct)
        if not kwargs.get('command'):
            if currentProduct is None:
                return FAILURE
            if previousProduct is None:
                return FAILURE
            self.command=['diff', '-r',
                          '%s/diffs' % currentProduct,
                          '%s/diffs' % previousProduct]
    
    def evaluateCommand(self, cmd):
        fileWarnings = self.getProperty('fileWarnings')
        if fileWarnings and len(fileWarnings) > 0:
            return WARNINGS
        '''We ignore failures here on purpose, since diff will 
           return 1(FAILURE) if it actually finds anything to output.
        '''
        return self.super_class.evaluateCommand(self, cmd)
    
    def createSummary(self, log):
        fileWarnings = []
        unmatchedFiles = []
        for line in log.readlines():
            # We want to know about files that are only in one build or the 
            # other, but we don't consider this worthy of a warning,
            # e.g. changed search plugins
            if line.startswith('Only'):
                unmatchedFiles.append(line)
                continue
            # These entries are nice to know about, but aren't fatal. We create
            # a separate warnings log for them.
            if line.startswith('> FAIL') or line.startswith('> Binary'):
                fileWarnings.append(line)
                continue

        if unmatchedFiles and len(unmatchedFiles) > 0:
            self.addCompleteLog('Only in...', "".join(unmatchedFiles))
                              
        self.setProperty('fileWarnings', fileWarnings)
        if fileWarnings and len(fileWarnings) > 0:
            self.addCompleteLog('Warnings', "".join(fileWarnings))



class SnippetComparison(ShellCommand):
    # Alphas/Betas are WARN'ed about in a releasetest vs release comparison
    # because they were shipped on the 'beta' channel, which means patcher
    # will not generate release channel snippets for them. This is OK, but we
    # need to ensure we don't fail the whole comparison because of it
    acceptable_warnings_regex = re.compile('WARN: [\w.]+/\w+/[0-9.]+[ab][0-9]+/.*release.* exists')
    # If we find +++ at the start of a line, let's assume it's output from
    # 'diff', which is always cause for concern.
    diff_regex = re.compile('^---')

    # command and name get overridden at runtime with whatever is passed to
    # ShellCommand.__init__
    def __init__(self, chan1, chan2, dir1, dir2, name=None, command=None,
                 timeout=10*60, description=['snippet', 'compare'],
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
        self.addFactoryArguments(chan1=chan1, chan2=chan2, dir1=dir1, dir2=dir2)
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
                unacceptable_warnings.extend(lines[i:i+2])
            if self.diff_regex.search(line):
                diffs.extend(lines[i:i+2])
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
