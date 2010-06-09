import re
import os.path

def buildTryCompleteMessage(attrs, packageDir, tinderboxTree):
    platform = ''
    task = ''
    got_revision = ''
    who = ''
    builder = attrs['builderName']

    try:
        got_revision = attrs['buildProperties']['got_revision']
    except KeyError:
        try:
            got_revision = attrs['buildProperties']['revision']
        except KeyError:
            got_revision = 'revision-not-set'
    try:
        who = attrs['buildProperties']['who']
    except KeyError:
        who = 'who-not-set'

    if 'Linux x86-64' in builder:
        platform = 'linux64'
    elif 'Linux' in builder:
        platform = 'linux'
    elif 'OS X 10.6' in builder:
        platform = 'mac64'
    elif 'OS X 10.5' in builder:
        platform = 'mac'
    elif 'WINNT' in builder:
        platform = 'win32'
    elif 'Maemo' in builder:
        platform = 'maemo'

    if 'test' in builder:
        task = 'test'
    elif 'talos' in builder:
        task = 'talos'
    else:
        task = 'build'

    url = packageDir % locals()

    if attrs['result'] == 'success':
        text = """\
Your Try Server %(task)s (%(got_revision)s) was successfully completed on \
%(platform)s on builder: %(builder)s.\n\n""" % locals()
    elif attrs['result'] == 'warnings':
        text = """\
Your Try Server %(task)s (%(got_revision)s) completed with warnings on \
%(platform)s on builder: %(builder)s.\n\n""" % locals()
    elif attrs['result'] == 'exception':
        text = """\
Your Try Server %(task)s (%(got_revision)s) hit an exception on \
%(platform)s on builder: %(builder)s.\n\n""" % locals()
    elif attrs['result'] == 'failure':
        text = """\
Your Try Server %(task)s (%(got_revision)s) failed to complete on \
%(platform)s on builder: %(builder)s.\n\n""" % locals()
    else:
        text = """\
Unknown issues arose during %(task)s (%(got_revision)s) on \
%(platform)s on builder: %(builder)s. Check with RelEng if you have questions.\n\n""" % locals()

    if attrs['result'] in ('success', 'warnings') and packageDir and 'who-not-set' not in who:
        text += "It should be available for download at %(url)s\n\n" % locals()
    if attrs['result'] in ('failure', 'exception') and 'who-not-set' not in who:
        text += "While some of the build steps haven't succeeded, your build package might have been \
successfully created and could be available for download at %(url)s\n\n" % locals()

    if task == 'test':
        text += "Summary of test results:\n"
        for log in attrs['logs']:
            if 'summary' not in log[0]:
                continue
            summary = ''.join(log[2]).replace('TinderboxPrint: ', '')
            summary = summary.replace('<br/>', ': ')
            text += '%s\n' % summary
        text += '\n'

    elif task == 'talos':
        text += "Summary of talos results:\n"
        for log in attrs['logs']:
            if 'summary' not in log[0]:
                continue
            for line in summary:
                if '"test"' in line:
                    test = re.findall('>t[a-zA-Z]+: \d+\.[0-9a-zA-Z]+', line)[0]
                    text += '%s\n' % test
        text += '\n'

    text += """Visit http://tinderbox.mozilla.org/showbuilds.cgi?tree=%(tinderboxTree)s to view the full logs.""" % locals()

    return (text, 'plain')

