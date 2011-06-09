from buildbot.process.buildstep import regex_log_evaluator

from buildbotcustom.steps.base import RetryingShellCommand
from buildbotcustom.status.errors import hg_errors


class MercurialCloneCommand(RetryingShellCommand):
    def __init__(self, log_eval_func=None, **kwargs):
        self.super_class = RetryingShellCommand
        if not log_eval_func:
            log_eval_func = lambda c,s: regex_log_evaluator(c, s, hg_errors)
        self.super_class.__init__(self, log_eval_func=log_eval_func, **kwargs)
