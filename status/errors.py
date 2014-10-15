import re

from buildbot.status.builder import FAILURE, RETRY, WARNINGS


def re_compile(s):
    return re.compile(s)

global_errors = ((re_compile("No space left on device"), RETRY),
                 # Bug 1018531: only RETRY with "Remote Device Error" if *not* preceded by "Caught Exception: "
                 (re_compile("(?<!INFO -  Caught Exception: )Remote Device Error"), RETRY),
                 (re_compile("devicemanager.DMError"), RETRY),
                 (re_compile("Connection to the other side was lost in a non-clean fashion"), RETRY),
                 (re_compile("program finished with exit code 80"), RETRY),
                 (re_compile("INFRA-ERROR"), RETRY),
                 (re_compile("Failure instance: Traceback (failure with no frames): <class 'twisted.spread.pb.PBConnectionLost'>"), RETRY),
                 )
hg_errors = ((re_compile("abort: HTTP Error 5\d{2}"), RETRY),
             (re_compile("abort: .*: no match found!"), RETRY),
             (re_compile("abort: Connection reset by peer"), RETRY),
             (re_compile("transaction abort!"), RETRY),
             (re_compile("abort: error:"), RETRY),
             )
purge_error = ((re_compile("Error: unable to free"), RETRY),)

update_verify_error = ((re_compile("FAIL"), FAILURE),)

permission_check_error = (
    (re_compile("WARN: target directory .* exists"), WARNINGS),
)

upload_errors = ((re_compile("Connection timed out"), RETRY),
                 (re_compile("Connection refused"), RETRY),
                 (re_compile("Connection reset by peer"), RETRY),
                 )

talos_hgweb_errors = ((re_compile("ERROR 500: Internal Server Error"), RETRY),
                      (re_compile("ERROR: We tried to download the talos.json file but something failed"), RETRY),
                      (re_compile("command timed out:"), RETRY),
                      )
