import re
import os.path

def buildTryChangeMessage(change, packageDir):
    got_revision = revision = change.revision[:12]
    who = change.who
    packageDir = packageDir % locals()
    msgdict = {"type": "plain"}
    msgdict['subject'] = "Try submission %(revision)s" % locals()
    msgdict['headers'] = {"In-Reply-To": "<try-%(revision)s>" % locals(),
                          "References": "<try-%(revision)s>" % locals(),
                          }
    msgdict["body"] = """\
Thanks for your try submission (http://hg.mozilla.org/try/pushloghtml?changeset=%(revision)s).  It's the best!

Watch https://tbpl.mozilla.org/?tree=Try&usebuildbot=1&rev=%(revision)s for your results to come in.

Builds and logs will be available at %(packageDir)s.

This directory won't be created until the first builds are uploaded, so please be patient.
""" % locals()

    return msgdict


