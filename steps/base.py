from buildbot.process.buildstep import LoggingBuildStep, regex_log_evaluator
from buildbot.process.properties import WithProperties
from buildbot.steps.shell import ShellCommand, SetProperty
from buildbot.steps.source import Mercurial as UpstreamMercurial
from buildbot.steps.trigger import Trigger
from buildbot.status.builder import worst_status, SUCCESS

from buildbotcustom.status.errors import global_errors, hg_errors

def addErrorCatching(obj):
    class C(obj):
        def evaluateCommand(self, cmd):
            lbs_status = obj.evaluateCommand(self, cmd)
            # If we don't have a custom log evalution function, run through
            # some global checks
            if self.log_eval_func is None:
                regex_status = regex_log_evaluator(cmd, self.step_status,
                                                   global_errors)
                return worst_status(lbs_status, regex_status)
            return lbs_status
    return C

LoggingBuildStep = addErrorCatching(LoggingBuildStep)
ShellCommand = addErrorCatching(ShellCommand)
SetProperty = addErrorCatching(SetProperty)
Trigger = addErrorCatching(Trigger)
ErrorCatchingMercurial = addErrorCatching(UpstreamMercurial)

class Mercurial(ErrorCatchingMercurial):
    def __init__(self, log_eval_func=None, **kwargs):
        self.super_class = ErrorCatchingMercurial
        if not log_eval_func:
            log_eval_func = lambda c,s: regex_log_evaluator(c, s, hg_errors)
        self.super_class.__init__(self, log_eval_func=log_eval_func, **kwargs)

def addRetryEvaluateCommand(obj):
    class C(obj):
        retryCommand = ['python', WithProperties('%(toolsdir)s/buildfarm/utils/retry.py'),
                        '-s', '1', '-r', '5']
        def __init__(self, command, retry=True, **kwargs):
            self.super_class = obj
            if retry:
                wrappedCommand = self.retryCommand + \
                               ['-t', kwargs.get('timeout', 1200)] + \
                               command
            else:
                wrappedCommand = command
            self.super_class.__init__(self, command=wrappedCommand, **kwargs)
            self.addFactoryArguments(command=command, retry=retry)

        def evaluateCommand(self, cmd):
            # When using retry.py we could have error messages show up in the log,
            # even if the command completes successfully on a later attempt.
            # Because of this, we have to assume success if the rc is 0
            if cmd.rc == 0:
                return SUCCESS
            # Otherwise, let our base class deal with it, which allows
            # for global error catching and overriden log_eval_func, still.
            return obj.evaluateCommand(self, cmd)
    return C

RetryingShellCommand = addRetryEvaluateCommand(ShellCommand)
RetryingSetProperty = addRetryEvaluateCommand(SetProperty)
