from buildbot.process.buildstep import regex_log_evaluator
from buildbot.steps.source import Mercurial
from buildbot.steps.shell import ShellCommand

import buildbotcustom.status.errors
reload(buildbotcustom.status.errors)

from buildbotcustom.status.errors import hg_errors

class EvaluatingMercurial(Mercurial):
    def __init__(self, log_eval_func=None, **kwargs):
        if not log_eval_func:
            log_eval_func = lambda c,s: regex_log_evaluator(c, s, hg_errors)
        Mercurial.__init__(self, log_eval_func=log_eval_func, **kwargs)


class MercurialCloneCommand(ShellCommand):
    def __init__(self, log_eval_func=None, **kwargs):
        if not log_eval_func:
            log_eval_func = lambda c,s: regex_log_evaluator(c, s, hg_errors)
        ShellCommand.__init__(self, log_eval_func=log_eval_func, **kwargs)
