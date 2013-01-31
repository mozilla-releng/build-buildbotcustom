import re
import time
import uuid


def getSupportedPlatforms():
    return ('linux', 'linuxqt', 'linux64',
            'win32', 'macosx', 'macosx64',
            'win64', 'android',
            'ics_armv7a_gecko',
            'gb_armv7a_gecko', 'linux32_gecko',
            'macosx64_gecko', 'win32_gecko',
            'linux64_gecko', 'linux32_gecko_localizer',
            'macosx64_gecko_localizer', 'win32_gecko_localizer',
            'linux64_gecko_localizer')


def getPlatformFtpDir(platform):
    platform_ftp_map = {
        'linux': 'linux-i686',
        'linux64': 'linux-x86_64',
        'macosx': 'mac',
        'macosx64': 'mac',
        'win32': 'win32',
        'win64': 'win64-x86_64',
        'android': 'android-r7',
    }
    return platform_ftp_map.get(platform)


def genBuildID(now=None):
    """Return a buildid based on the current time"""
    if not now:
        now = time.time()
    return time.strftime("%Y%m%d%H%M%S", time.localtime(now))


def genBuildUID():
    """Return a unique build uid"""
    return uuid.uuid4().hex


def incrementBuildID(buildID):
    """Add 1 second to a buildID, handling rollovers to next minute/hour/etc"""
    epoch = time.mktime(time.strptime(buildID, "%Y%m%d%H%M%S"))
    return genBuildID(epoch + 1)


def normalizeName(name, product=None):
    prefix = ''
    if product != None and 'thunderbird' in product:
        prefix = 'tb-'

    mappings = {
        'mozilla': 'm',
        'comm': 'c',
        'central': 'cen',
        '1.9.1': '191',
        '1.9.2': '192',
        'places': 'plc',
        'electrolysis': 'e10s',
        'jaegermonkey': 'jm',
        'shadow': 'sh',
        'mobile': 'mb',
        'desktop': '',
        'debug': 'dbg',
        'xulrunner': 'xr',
        'build': 'bld',
        'linux': 'lnx',
        'win32': 'w32',
        'win64': 'w64',
        'macosx': 'osx',
        'macosx64': 'osx64',
        'linux64': 'lnx64',
        'android': 'andrd',
        'release': 'rel',
        'mochitest': 'm',
        'browser-chrome': 'b-c',
        'other': 'oth',
        'browser': 'br',
        'nightly': 'ntly',
        'tryserver': 'try',
        'cedar': 'ced',
        'birch': 'bir',
        'maple': 'map',
        'leopard': 'leo',
        'snowleopard': 'snow',
        'fedora': 'fed',
        'fedora64': 'fed64',
        'ubuntu64': 'ub64',
        'ubuntu32': 'ub32',
        'repack': 'rpk',
        'alder': 'a',
        'holly': 'h',
        'larch': 'l',
        'accessibility': 'a11y',
        'inbound': 'in',
        'devtools': 'dev',
        'services': 'srv',
        'private-browsing': 'pb',
        'gecko': 'g',
        'localizer': 'lz',
    }
    for word, replacement in mappings.iteritems():
        # Regexes are slow, so make sure the word is there at all before
        # trying to do a substitution.
        if word in name:
            # Match strings that...
            regex = re.compile(
                r'(-|_|\A)' +   # Are the start of the string or after a separator
                ('%s' % word) + # Contain the word we're looking
                r'(-|_|\Z)'     # And don't have anything other the end of the string
                                # or a separator atfer them
            )
            name = regex.sub(r'\1%s\2' % replacement, name)
    # Not sure why we do this, but apparently we replace all the underscores
    # with hyphens...
    name = name.replace('_', '-')
    return prefix + name
