import re
import time
import uuid


def getSupportedPlatforms():
    return ('linux', 'linuxqt', 'linux64',
            'win32', 'macosx', 'macosx64',
            'win64', 'android',
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


def normalizeName(name, product=None, min_=30, max_=30, filler='0'):
    """Shortens names and normalizes them within specified minimum
       and maximum lengths. If names need lengthening, 'filler' is
       appended until they reach the minimum length.
       See https://bugzilla.mozilla.org/show_bug.cgi?id=827306 for background.
    """
    # XXX: Remove me when esr17 dies.
    # We need a very very short directory name here because:
    # 1) ESR builds have especially long paths compared to other branches.
    # 2) Pymake doesn't work on esr17 which makes it possible to hit maximum
    #    path length issues when working with certain mochitests.
    # https://bugzilla.mozilla.org/show_bug.cgi?id=799095#c7
    if name == 'release-comm-esr17-win32_build':
        return 'zzz'
    elif name == 'release-mozilla-esr17-win32_build':
        return 'yyy'
    elif name == 'release-comm-release-win32_build':
        return 'ggg'
    origName = name
    prefix = ''
    if product != None and 'thunderbird' in product:
        prefix = 'tb-'

    mappings = {
        'mozilla': 'm',
        'comm': 'c',
        'central': 'cen',
        'mobile': 'mb',
        'desktop': '',
        'debug': 'd',
        'xulrunner': 'xr',
        'build': 'bld',
        'linux': 'lx',
        'win32': 'w32',
        'win64': 'w64',
        'macosx': 'osx',
        'macosx64': 'osx64',
        'linux64': 'l64',
        'android': 'and',
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
        'cypress': 'cy',
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
        'services': 'srv',
        'gecko': 'g',
        'localizer': 'lz',
        'blocklistupdate': 'blu',
        'armv6': 'a6',
        'armv7a': 'a7',
        'ionmonkey': 'ion',
        'system': 'sys',
        'panda': 'p',
        'b2g18': 'b18',
        'v1_0_0': '100',
        'v1_0_1': '101',
        'v1_1_0': '110',
        'standalone': 'sa',
        'thunderbird': 'tb',
        'checksums': 'sums',
        'update_verify': 'uv',
        'spidermonkey': 'sm',
        'warnaserr': 'we',
        'warnaserrdebug': 'wed',
        'rootanalysis': 'ra',
        'generational': 'ggc',
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
    name = prefix + name
    # XXX: Remove me when esr17 is dead. Nasty hack to avoid shortening
    # this branches' directories because the build system can't handle the
    # padded version. Can also be removed if we manage to turn pymake on for it.
    # comm-release is in here because we had to turn pymake off for it in
    # bug 841898, because we do releases for "comm-release" off of esr17
    # and can't flip it just for those. this should be fixed before tb17.0.4 builds
    if 'esr17' in origName or 'comm-release' in origName:
        return name

    if len(name) > max_:
        msg = 'Cannot shorten "%s" to maximum length (%d). Got to: %s' % (
            origName, max_, name)
        raise ValueError(msg)
    # Add a seperator before the padding if necessary
    if len(name) < min_:
        name += '-'
    while len(name) < min_:
        name += filler
    return name
