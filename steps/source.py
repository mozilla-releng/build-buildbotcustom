from buildbot.process.buildstep import regex_log_evaluator

from buildbotcustom.steps.base import ShellCommand
from buildbotcustom.status.errors import hg_errors


class MercurialCloneCommand(ShellCommand):
    def __init__(self, log_eval_func=None, **kwargs):
        if not log_eval_func:
            log_eval_func = lambda c,s: regex_log_evaluator(c, s, hg_errors)
        ShellCommand.__init__(self, log_eval_func=log_eval_func, **kwargs)
