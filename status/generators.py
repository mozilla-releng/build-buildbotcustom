import re
import os.path

def buildTryChangeMessage(change, packageDir):
    got_revision = revision = change.revision[:12]
    who = change.who
    branch = change.branch
    tree = "Try"
    if 'comm' in branch:
        tree = "ThunderbirdTry"
    packageDir = packageDir % locals()
    msgdict = {"type": "plain"}
    msgdict['subject'] = "%(tree)s submission %(revision)s" % locals()
    msgdict['headers'] = {"In-Reply-To": "<%(branch)s-%(revision)s>" % locals(),
                          "References": "<%(branch)s-%(revision)s>" % locals(),
                          }
    msgdict["body"] = """\
Thanks for your try submission (http://hg.mozilla.org/%(branch)s/pushloghtml?changeset=%(revision)s).  It's the best!

Watch https://tbpl.mozilla.org/?tree=%(tree)s&rev=%(revision)s for your results to come in.

Builds and logs will be available at %(packageDir)s.

This directory won't be created until the first builds are uploaded, so please be patient.
""" % locals()

    return msgdict


