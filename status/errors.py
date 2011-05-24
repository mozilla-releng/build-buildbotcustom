import re

from buildbot.status.builder import EXCEPTION, FAILURE, RETRY

global_errors = ((re.compile("No space left on device"), RETRY),
                 (re.compile("Remote Device Error"), EXCEPTION),
                )
hg_errors = ((re.compile("abort: HTTP Error 5\d{2}"), RETRY),
             (re.compile("abort: .*: no match found!"), RETRY),
             (re.compile("abort: Connection reset by peer"), RETRY),
             (re.compile("transaction abort!"), RETRY),
            )
purge_error = ((re.compile("Error: unable to free"), RETRY),)

update_verify_error = ((re.compile("FAIL"), FAILURE),)
