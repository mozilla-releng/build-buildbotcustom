import re

from buildbot.status.builder import EXCEPTION, FAILURE, RETRY, WARNINGS

global_errors = ((re.compile("No space left on device"), RETRY),
                 (re.compile("Remote Device Error"), RETRY),
                 (re.compile("devicemanager.DMError"), RETRY),
                 (re.compile("Connection to the other side was lost in a non-clean fashion"), RETRY),
                 (re.compile("program finished with exit code 80"), RETRY),
                )
hg_errors = ((re.compile("abort: HTTP Error 5\d{2}"), RETRY),
             (re.compile("abort: .*: no match found!"), RETRY),
             (re.compile("abort: Connection reset by peer"), RETRY),
             (re.compile("transaction abort!"), RETRY),
            )
purge_error = ((re.compile("Error: unable to free"), RETRY),)

update_verify_error = ((re.compile("FAIL"), FAILURE),)

permission_check_error = (
    (re.compile("WARN: target directory .* exists"), WARNINGS),
)

upload_errors = ((re.compile("Connection timed out"), RETRY),
                 (re.compile("Connection refused"), RETRY),
                 (re.compile("Connection reset by peer"), RETRY),
                )
