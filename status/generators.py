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
        got_revision = 'unknown'
    try:
        who = attrs['buildProperties']['who']
    except KeyError:
        who = 'unknown'

    if 'Linux' in builder:
        platform = 'linux'
    elif 'OS X' in builder:
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
Your Try Server %(task)s (%(got_revision)s) was successfully completed on \
%(platform)s on builder: %(builder)s.\n\n""" % locals()
    else:
        text = """\
Your Try Server %(task)s (%(got_revision)s) was successfully completed on \
%(platform)s on builder: %(builder)s.\n\n""" % locals()

    if attrs['result'] in ('success', 'warnings') and packageDir:
        text += "It should be available for download at %(url)s\n\n" % locals()
    if attrs['result'] in ('failure', 'warnings') and 'unknown' not in who:
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

