import re
import os.path

def buildTryCompleteMessage(attrs, packageDir, tinderboxTree):
    platform = ''
    task = ''
    identifier = ''
    who = ''
    builder = attrs['builderName']

    try:
        identifier = attrs['buildProperties']['identifier']
    except KeyError:
        identifier = 'unknown'
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
    elif 'WinMo' in builder:
        platform = 'winmo'
    elif 'Maemo' in builder:
        platform = 'maemo'

    if 'unit test' in builder:
        task = 'unit test'
    elif 'talos' in builder:
        task = 'talos'
    else:
        task = 'build'

    url = packageDir % locals()

    if attrs['result'] == 'success':
        text = """\
Your Try Server %(task)s (%(identifier)s) was successfully completed on \
%(platform)s.  """ % locals()
    elif attrs['result'] == 'warnings':
        text = """\
Your Try Server %(task)s (%(identifier)s) completed with warnings on \
%(platform)s.  """ % locals()
    else:
        text = """\
Your Try Server %(task)s (%(identifier)s) failed to complete on \
%(platform)s.\n\n""" % locals()

    if attrs['result'] in ('success', 'warnings') and packageDir:
        text += "It should be available for download at %(url)s\n\n" % locals()

    if task == 'unit test':
        text += "Summary of unittest results:\n"
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

    text += """Visit %(tinderboxTree)s to view the full logs.""" % locals()

    return (text, 'plain')

