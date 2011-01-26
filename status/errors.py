import re

from buildbot.status.builder import EXCEPTION, RETRY

global_errors = ((re.compile("No space left on device"), RETRY),
                 (re.compile("Remote Device Error"), EXCEPTION),
                )
hg_errors = ((re.compile("abort: HTTP Error 5\d{2}"), RETRY),
             (re.compile("abort: .*: no match found!"), RETRY)
            )
purge_error = ((re.compile("Error: unable to free"), RETRY),)
