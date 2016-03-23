import re
import time
import uuid
from distutils.version import LooseVersion, StrictVersion


def getSupportedPlatforms():
    return ('linux', 'linuxqt', 'linux64',
            'win32', 'macosx', 'macosx64',
            'win64', 'android',
            'gb_armv7a_gecko')


def getPlatformFtpDir(platform):
    platform_ftp_map = {
        'linux': 'linux-i686',
        'linux64': 'linux-x86_64',
        'macosx': 'mac',
        'macosx64': 'mac',
        'win32': 'win32',
        'win64': 'win64',
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
        'build': 'bld',
        'linux': 'lx',
        'win32': 'w32',
        'win64': 'w64',
        'macosx': 'osx',
        'macosx64': 'm64',
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
        'b2g37': 'b37',
        'b2g44': 'b44',
        'v2_5': '25',
        'standalone': 'sa',
        'thunderbird': 'tb',
        'partner': 'pner',
        'checksums': 'sums',
        'postrelease': 'pr',
        'spidermonkey': 'sm',
        'warnaserr': 'we',
        'warnaserrdebug': 'wed',
        'rootanalysis': 'ra',
        'generational': 'ggc',
        'emulator': 'emu',
        'hamachi': 'ham',
        'wasabi': 'wsb',
        'graphics': 'gfx',
        'flame': 'flm',
        'dolphin': 'dph',
        'nexus-5': 'n5',
        'firefox_tag_source': 'fx_tag_src',
        'firefox_tag_l10n': 'fx_tag_l10n',
        'firefox': 'fx',
        'fennec_tag_source': 'm_tag_src',
        'fennec_tag_l10n': 'm_tag_l10n',
        'thunderbird_tag_source': 't_tag_src',
        'thunderbird_tag_l10n': 't_tag_l10n',
        'start_uptake_monitoring': 'ut',
        'final': 'fnl',
        'verification': 'vrfy',
        'shipping': 'shp',
        'update': 'u',
        'verify': 'v',
        'updates': 'upds',
        'tests': 't',
    }
    for word in sorted(mappings):
        replacement = mappings[word]
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


def getPreviousVersion(version, partialVersions):
    """ The patcher config bumper needs to know the exact previous version
    We use LooseVersion for ESR because StrictVersion can't parse the trailing
    'esr', but StrictVersion otherwise because it can sort X.0bN lower than X.0.
    The current version is excluded to avoid an error if build1 is aborted
    before running the updates builder and now we're doing build2
    """
    if version.endswith('esr'):
        return str(max(LooseVersion(v) for v in partialVersions if v != version))
    else:
        # StrictVersion truncates trailing zero in versions with more than 1
        # dot. Compose a structure that will be sorted by StrictVersion and
        # return untouched version
        composed = sorted([(v, StrictVersion(v)) for v in partialVersions if v
                           != version], key=lambda x: x[1], reverse=True)
        return composed[0][0]
