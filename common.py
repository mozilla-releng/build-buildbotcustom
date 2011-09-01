import time, uuid

def getCodesighsPlatforms():
    return ('linux', 'linuxqt','linux64',
            'win32', 'win64', 'macosx', 'macosx64')

def getSupportedPlatforms():
    return ('linux', 'linuxqt','linux64',
            'win32', 'macosx', 'macosx64',
            'win64', 'android')

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

def reallyShort(name):
    mappings = {
        'mozilla': 'm',
        'central': 'cen',
        '1.9.1': '191',
        '1.9.2': '192',
        'places': 'plc',
        'electrolysis': 'e10s',
        'jaegermonkey': 'jm',
        'shadow': 'sh',
        'mobile': 'mb',
        'desktop': None,
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
        'mochitests': 'mochi',
        'mochitest': 'm',
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
        'repack': 'rpk',
        'alder': 'a',
        'holly': 'h',
        'larch': 'l',
        'accessibility': 'a11y',
        'inbound': 'in',
        'devtools': 'dev',
        'services': 'srv',
        'private-browsing': 'pb',
    }
    hyphen_seperated_words = name.split('-')
    words = []
    for word in hyphen_seperated_words:
        space_seperated_words = word.split('_')
        for word in space_seperated_words:
            words.extend(word.split(' '))
    new_words = []
    for word in words:
        if word in mappings.keys():
            if mappings[word]:
                new_words.append(mappings[word])
        else:
            new_words.append(word)
    return '-'.join(new_words)

