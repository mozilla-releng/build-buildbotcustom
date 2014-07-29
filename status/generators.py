import re
import os.path


def buildTryChangeMessage(change, packageDir):
    got_revision = revision = change.revision[:12]
    who = change.who
    branch = change.branch
    tree = "Try"
    if 'comm' in branch:
        tree = "Thunderbird-Try"
    packageDir = packageDir % locals()
    msgdict = {"type": "plain"}
    msgdict['subject'] = "%(tree)s submission %(revision)s" % locals()
    msgdict['headers'] = {"In-Reply-To": "<%(branch)s-%(revision)s>" % locals(),
                          "References": "<%(branch)s-%(revision)s>" % locals(),
                          }
    msgdict["body"] = """\
Thank you for your try submission. It's the best!

Results will be displayed on TBPL as they come in:
https://tbpl.mozilla.org/?tree=%(tree)s&rev=%(revision)s

Alternatively, view them on Treeherder (experimental):
https://treeherder.mozilla.org/ui/#/jobs?repo=%(tree)s&revision=%(revision)s

Once completed, builds and logs will be available at:
%(packageDir)s
""" % locals()

    return msgdict
