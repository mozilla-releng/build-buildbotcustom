from buildbot.process.buildstep import LoggingBuildStep, regex_log_evaluator
from buildbot.steps.shell import ShellCommand, SetProperty
from buildbot.steps.source import Mercurial as UpstreamMercurial
from buildbot.steps.trigger import Trigger
from buildbot.status.builder import worst_status

from buildbotcustom.status.errors import global_errors, hg_errors

def addErrorCatching(obj):
    class C(obj):
        def evaluateCommand(self, cmd):
            lbs_status = obj.evaluateCommand(self, cmd)
            regex_status = regex_log_evaluator(cmd, self.step_status,
                                               global_errors)
            return worst_status(lbs_status, regex_status)
    return C

LoggingBuildStep = addErrorCatching(LoggingBuildStep)
ShellCommand = addErrorCatching(ShellCommand)
SetProperty = addErrorCatching(SetProperty)
Trigger = addErrorCatching(Trigger)
ErrorCatchingMercurial = addErrorCatching(UpstreamMercurial)

class Mercurial(ErrorCatchingMercurial):
    def __init__(self, log_eval_func=None, **kwargs):
        if not log_eval_func:
            log_eval_func = lambda c,s: regex_log_evaluator(c, s, hg_errors)
        ErrorCatchingMercurial.__init__(self, log_eval_func=log_eval_func, **kwargs)
