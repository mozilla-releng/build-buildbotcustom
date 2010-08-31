# Mozilla Try Parser
# Contributor(s):
#   Lukas Blakk <lsblakk@mozilla.com>

import argparse, re

from twisted.python import log

import buildbotcustom.valid_builders
reload(buildbotcustom.valid_builders)

from buildbotcustom.valid_builders import PRETTY_NAMES, DESKTOP_PLATFORMS, MOBILE_PLATFORMS, \
                           TALOS_SUITES, UNITTEST_SUITES

'''Given a list of arguments from commit message or info file
   returns only those builder names that should be built.'''

def processMessage(message):
    match = re.search('try:',str(message))
    if match:
        message = message.strip().split('try: ', 1)
        message = message[1].split(' ')
    else:
        message =[""]
    return message

def getDesktopBuilders(platforms, builderNames, buildTypes):
    desktopBuilders = []

    if platforms != 'none':
        for buildType in buildTypes:
            if buildType == 'opt':
                buildType = 'build'
            if buildType == 'debug':
                buildType = 'leak test build'

            for platform in platforms:
                platform = 'desktop_' + platform
                if platform in PRETTY_NAMES.keys():
                    custom_builder = "%s tryserver %s" % (PRETTY_NAMES[platform], buildType)
                    if custom_builder in builderNames:
                        desktopBuilders.extend([custom_builder])
    return desktopBuilders

def getMobileBuilders(platforms, builderNames):
    mobileBuilders = []

    if platforms != 'none':
        for platform in platforms:
            if platform in ('win32', 'macosx', 'linux'):
                platform = 'mobile_' + platform
            if platform in PRETTY_NAMES.keys():
                custom_builder = "%s tryserver build" % (PRETTY_NAMES[platform])
                if custom_builder in builderNames:
                    mobileBuilders.extend([custom_builder])
    return mobileBuilders

def getTestBuilders(platforms, testType, tests, builderNames, buildTypes):
    testBuilders = []
    # for all possible suites, add in the builderNames for that platform
    if tests != 'none':
        if testType == "test":
            for buildType in buildTypes:
                for platform in platforms:
                    for test in tests:
                        # we only do unittests on WINNT 6.1
                        if platform == 'win32':
                            custom_builder = "%s tryserver %s %s %s" % (PRETTY_NAMES[platform][1], buildType, testType, test)
                        else:
                            custom_builder = "%s tryserver %s %s %s" % (PRETTY_NAMES[platform], buildType, testType, test)
                        if custom_builder in (builderNames):
                            testBuilders.extend([custom_builder])
        if testType == "talos":
            for platform in platforms:
                for test in tests:
                    if platform == 'win32':
                        # we still do talos runs on win2k3 ie: WINNT 5.1
                        for w in PRETTY_NAMES['win32']:
                            custom_builder = "%s tryserver %s %s" % (w, testType, test)
                            if custom_builder in (builderNames):
                                testBuilders.extend([custom_builder])
                    else:
                        custom_builder = "%s tryserver %s %s" % (PRETTY_NAMES[platform], testType, test)
                        if custom_builder in (builderNames):
                            testBuilders.extend([custom_builder])
    return testBuilders

def TryParser(message, builderNames):

    parser = argparse.ArgumentParser(description='Pass in a commit message and a list \
                                     and tryParse populates the list with the builderNames\
                                     that need schedulers.')

    parser.add_argument('--build',
                        default='do',
                        dest='build',
                        help='accepts the build types requested ')
    parser.add_argument('--p',
                        default='all',
                        dest='desktop',
                        help='provide a list of desktop platforms, or specify none (default is all)')
    parser.add_argument('--m',
                        default='all',
                        dest='mobile',
                        help='provide a list of mobile platform, or specify none (default is all)')
    parser.add_argument('--u',
                        default='all',
                        dest='test',
                        help='provide a list of unit tests, or specify all (default is None)')
    parser.add_argument('--t',
                        default='none',
                        dest='talos',
                        help='provide a list of talos tests, or specify all (default is None)')

    (options, unknown_args) = parser.parse_known_args(processMessage(message))

    # Build options include a possible override of 'all' to get a buildset that matches m-c
    if options.build == 'do' or options.build == 'od':
        options.build = ['opt', 'debug']
    elif options.build == 'd':
        options.build = ['debug']
    elif options.build == 'o':
        options.build = ['opt']
    elif options.build == 'all':
        options.build = ['opt', 'debug']
        options.desktop = 'all'
        options.mobile = 'all'
        options.test = 'all'
        options.talos = 'all'
    else:
        # for any input other than do/od, d, o, all set to default
        options.build = ['opt','debug']

    if options.desktop == 'all':
        options.desktop = DESKTOP_PLATFORMS
    elif options.desktop != 'none':
        options.desktop = options.desktop.split(',')

    if options.mobile == 'all':
        options.mobile = MOBILE_PLATFORMS
    elif options.mobile != 'none':
        options.mobile = options.mobile.split(',')

    if options.test == 'all':
        options.test = UNITTEST_SUITES
    elif options.test != 'none':
        options.test = options.test.split(',')
    if options.talos == 'all':
        options.talos = TALOS_SUITES
    elif options.talos != 'none':
        options.talos = options.talos.split(',')

    # Get the custom builder names
    customBuilderNames = getDesktopBuilders(options.desktop, builderNames, options.build)
    customBuilderNames.extend(getMobileBuilders(options.mobile, builderNames))
    customBuilderNames.extend(getTestBuilders(options.desktop, "test", options.test, 
                              builderNames, options.build))
    customBuilderNames.extend(getTestBuilders(options.desktop, "talos", options.talos, builderNames, 
                              options.build))

    return customBuilderNames