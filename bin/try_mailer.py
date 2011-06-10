#!/usr/bin/python
"""%prog [options] host builder_path build_number

Uploads logs to the given host, and then sends an email to the build's owner
"""
import subprocess, sys, os, re
import cPickle
from email.message import Message
from email.utils import formatdate

from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, EXCEPTION, RETRY

def getBuild(builder_path, build_number):
    build_path = os.path.join(builder_path, build_number)

    if not os.path.exists(build_path):
        raise ValueError("Couldn't find %s" % build_path)

    class FakeBuilder:
        basedir = builder_path
        name = os.path.basename(builder_path)

    build = cPickle.load(open(build_path))
    build.builder = FakeBuilder()
    return build

def uploadLog(args):
    """Uploads the build log, and returns the URL to it"""
    my_dir = os.path.abspath(os.path.dirname(__file__))
    cmd = [sys.executable, "%s/log_uploader.py" % my_dir] + args
    devnull = open(os.devnull)

    print "Running", cmd

    proc = subprocess.Popen(cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=devnull,)

    retcode = proc.wait()
    output = proc.stdout.read().strip()
    print output

    # Look for URLs
    url = re.search("http://\S+", output)
    if url:
        return url.group(), retcode
    return None, retcode

def makeTryMessage(build, log_url):
    builder = build.builder.name

    props = build.getProperties()

    if 'who' in props:
        who = props['who']
    else:
        users = build.getResponsibleUsers()
        if users:
            who = users[0]
        else:
            raise ValueError("I don't know who did this build")

    if 'got_revision' in props:
        revision = props['got_revision'][:12]
    elif 'revision' in props:
        revision = props['revision'][:12]
    else:
        revision = 'unknown'

    if 'test' in builder:
        task = 'test'
    else:
        task = 'build'

    result = build.getResults()

    if result == SUCCESS:
        subject = "Try submission %(revision)s" % locals()
        result_msg = "was successfully completed"
    elif result == WARNINGS:
        subject = "Try submission %(revision)s - warnings" % locals()
        result_msg = "completed with warnings"
    elif result == EXCEPTION:
        subject = "Try submission %(revision)s - errors" % locals()
        result_msg = "hit a buildbot exception"
    elif result == FAILURE:
        subject = "Try submission %(revision)s - errors" % locals()
        result_msg = "failed to complete"
    else:
        subject = "Try submission %(revision)s - errors" % locals()
        result_msg = "had unknown problem (%s)" % result

    text = """\
Your Try Server %(task)s (%(revision)s) %(result_msg)s on builder %(builder)s.\n\n""" % locals()

    if 'packageUrl' in props:
        url = props['packageUrl'].replace('://stage', '://ftp')
        text += "It should be available for download at <a href=\"%(url)s\">%(url)s</a>\n\n" % locals()

    if task == 'test':
        text += "Summary of test results:\n\n"
        for log in build.getLogs():
            if 'summary' not in log.getName():
                continue
            summary = log.getText().replace('TinderboxPrint:', '')
            summary = summary.replace('<br>', '')
            summary = re.sub("\n\n*", "\n", summary)
            text += '%s\n\n' % summary

    if log_url:
        log_url = log_url.replace('://stage', '://ftp')
        text += "The full log for this %(task)s run is available at <a href=\"%(log_url)s\">%(log_url)s</a>.\n" % locals()
    else:
        text += "Please check <a href=\"http://tbpl.mozilla.org/?tree=Try&rev=%(revision)s\">Tinderbox Pushlog</a> for your logs.\n" % locals()

    text = re.sub("\n", "<br>\n", text)

    headers = {"In-Reply-To": "<try-%(revision)s>" % locals(),
                "References": "<try-%(revision)s>" % locals(),
              }

    return dict(
        subject=subject,
        body=text,
        headers=headers,
        author=who,
        type='html',
        )

def formatMessage(msgdict, from_, to):
    m = Message()
    m.set_payload(msgdict['body'])
    m.set_type('text/%s' % msgdict.get('type', 'plain'))
    m['Date'] = formatdate(localtime=True)
    m['Subject'] = msgdict['subject']
    m['From'] = from_
    m['To'] = ", ".join(to)
    for k,v in msgdict['headers'].items():
        if k not in m:
            m[k] = v
    return m

if __name__ == '__main__':
    from argparse import ArgumentParser
    from smtplib import SMTP
    parser = ArgumentParser()
    parser.add_argument("-f", "--from", dest="from_", help="from email address", required=True)
    parser.add_argument("-t", "--to", dest="to", help="to email address", action='append')
    parser.add_argument("--to-author", dest="to_author", help="send mail to build's owner", action="store_true")
    parser.set_defaults(
        to_author=False,
        to=[],
        from_=None,
        )

    options, args = parser.parse_known_args()

    if not options.to and not options.to_author:
        parser.error("You must specify --to, or --to-author")

    log_url, exit_code = uploadLog(args)
    print

    tm_parser = ArgumentParser()
    tm_parser.add_argument("-e", "--all-emails", dest="all_emails", help="request all emails", action="store_true")
    tm_parser.add_argument("-n", "--no-emails", dest="silence", help="request no emails at all", action="store_true")
    tm_parser.set_defaults(
        all_emails=False,
        silence=False,
        )

    builder_path, build_number = args[-2:]
    build = getBuild(builder_path, build_number)

    # check the commit message for syntax regarding email prefs
    match = re.search("try: ", build.source.changes[-1].comments)
    comment_args = ""
    if match:
        comment_args = build.source.changes[-1].comments.split("try: ")[1].split()
    tm_options, args = tm_parser.parse_known_args(comment_args)

    # Let's check the results to see if we need the message
    result = build.getResults()
    # if silence, never make the message
    # if all emails, alway make the message
    # else default is failures only
    msgdict = None
    # Generate the message
    if not tm_options.silence:
        if tm_options.all_emails:
            msgdict = makeTryMessage(build, log_url)
        else:
            if result != SUCCESS:
                msgdict = makeTryMessage(build, log_url)

    if options.to_author:
        options.to.append(msgdict['author'])

    # Send it!
    if msgdict != None:
        msg = formatMessage(msgdict, options.from_, options.to)
        print msg

        s = SMTP()
        s.connect()
        s.sendmail(options.from_, options.to, msg.as_string())

    sys.exit(exit_code)
