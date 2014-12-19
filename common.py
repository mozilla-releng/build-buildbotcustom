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
    origName = name
    prefix = ''
    if product is not None and 'thunderbird' in product:
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
        'system': 'sys',
        'panda': 'p',
        'b2g30': 'b30',
        'b2g32': 'b32',
        'b2g34': 'b34',
        'v1_4': '14',
        'v2_0': '20',
        'v2_1': '21',
        'v2_1s': '21s',
        'standalone': 'sa',
        'thunderbird': 'tb',
        'partner': 'pner',
        'checksums': 'sums',
        'update_verify': 'uv',
        'postrelease': 'pr',
        'spidermonkey': 'sm',
        'warnaserr': 'we',
        'warnaserrdebug': 'wed',
        'rootanalysis': 'ra',
        'generational': 'ggc',
        'emulator': 'emu',
        'hamachi': 'ham',
        'wasabi': 'wsb',
        'nonunified': 'nu',
        'graphics': 'gfx',
        'flame': 'flm',
        'firefox_tag_source': 'fx_tag_src',
        'firefox_tag_l10n': 'fx_tag_l10n',
        'fennec_tag_source': 'm_tag_src',
        'fennec_tag_l10n': 'm_tag_l10n',
        'thunderbird_tag_source': 't_tag_src',
        'thunderbird_tag_l10n': 't_tag_l10n',
    }
    for word, replacement in mappings.iteritems():
        # Regexes are slow, so make sure the word is there at all before
        # trying to do a substitution.
        if word in name:
            # Match strings that...
            regex = re.compile(
                r'(-|_|\A)' +    # Are the start of the string or after a separator
                ('%s' % word) +  # Contain the word we're looking
                r'(-|_|\Z)'      # And don't have anything other the end of the string
                                 # or a separator atfer them
            )
            name = regex.sub(r'\g<1>%s\g<2>' % replacement, name)
    name = prefix + name

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
