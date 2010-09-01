import re

from buildbot.status.builder import EXCEPTION, RETRY

hg_errors = ((re.compile("abort: HTTP Error \d{3}"), RETRY),
             (re.compile("abort: .*: no match found!"), RETRY)
            )
