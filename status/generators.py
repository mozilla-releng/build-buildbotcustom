import re


def buildTryChangeMessage(change, packageDir):
    got_revision = revision = change.revision[:12]
    who = change.who
    tree = change.branch
    packageDir = packageDir % locals()
    msgdict = {"type": "plain"}
    msgdict['subject'] = "%(tree)s submission %(revision)s" % locals()
    msgdict['headers'] = {"In-Reply-To": "<%(tree)s-%(revision)s>" % locals(),
                          "References": "<%(tree)s-%(revision)s>" % locals(),
                          }
    msgdict["body"] = """\
Thank you for your try submission. It's the best!

Results will be displayed on Treeherder as they come in:
https://treeherder.mozilla.org/#/jobs?repo=%(tree)s&revision=%(revision)s

Once completed, builds and logs will be available at:
%(packageDir)s
""" % locals()

    commitTitles = change.properties.getProperty('commit_titles')
    if commitTitles:
        title = getSensibleCommitTitle(commitTitles)
        allTitles = '\n  * '.join(commitTitles)

        msgdict['subject'] += ': %(title)s' % locals()
        msgdict['body'] += """\

Summary:
  * %(allTitles)s
""" % locals()

    return msgdict


def getSensibleCommitTitle(titles):
    """
    Returns the first non-trychooser title with unnecessary cruft removed.
    """
    for title in titles:
        # Remove trychooser syntax.
        title = re.sub(r'\btry: .*', '', title)

        # Remove MQ cruft.
        title = re.sub(r'^(imported patch|\[mq\]:) ', '', title)

        # Remove review, feedback, etc. annotations.
        title = re.sub(r'\b(r|sr|f|a)[=\?].*', '', title)

        # Remove trailing punctuation and whitespace.
        title = re.sub(r'[;,\-\. ]+$', '', title).strip()

        if len(title) > 2:
            return title

    return titles[0]
