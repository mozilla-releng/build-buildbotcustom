# Mozilla Try Parser
# Contributor(s):
#   Lukas Blakk <lsblakk@mozilla.com>

import argparse, re

from twisted.python import log

'''Given a list of arguments from commit message or info file
   returns only those builder names that should be built.'''

def expandTestSuites(user_suites,valid_suites):
    test_suites = []
    for u in user_suites:
        if u == 'mochitests':
            for v in valid_suites:
                if v.startswith('mochitest'):
                    test_suites.append(v)
        elif u == 'mochitest-o':
            for v in valid_suites:
                if re.search(u,v):
                    test_suites.append(v)
        elif u.startswith('mochitest-'):
            num = u.split('-')[1]
            for v in valid_suites:
                if v.startswith('mochitest') and re.search(num,v.split('/')[0]):
                    test_suites.append(v)
        else:
            # validate other test names
            if u in valid_suites:
                test_suites.append(u)
    return test_suites

def processMessage(message):
    match = re.search('try:',str(message))
    if match:
        message = message.strip().split('try: ', 1)
        message = message[1].split(' ')
    else:
        message =[""]
    return message

def getPlatformBuilders(user_platforms, builderNames, buildTypes, prettyNames):
    platformBuilders = []

    if user_platforms != 'none':
    # if user wants od - the platforms have -debug in them
        for buildType in buildTypes:
            for platform in user_platforms:
              if buildType == 'debug':
                  platform += '-debug'
              if platform in prettyNames.keys():
                  custom_builder = prettyNames[platform]
                  if custom_builder in builderNames and custom_builder not in platformBuilders:
                      platformBuilders.extend([custom_builder])
    return platformBuilders

def getTestBuilders(platforms, testType, tests, builderNames, buildTypes, prettyNames, unittestPrettyNames):
    testBuilders = []
    # for all possible suites, add in the builderNames for that platform
    if tests != 'none':
        if testType == "test":
            for buildType in buildTypes:
                for platform in platforms:
                    # this is to catch debug unittests triggered on the build master
                    # if the user asks for win32 with -b d
                    if buildType == 'debug' and not platform.endswith('debug'):
                        platform += '-debug'
                    if platform in prettyNames.keys():
                        for test in tests:
                            for slave_platform in prettyNames[platform]:
                                custom_builder = "%s tryserver %s %s %s" % (slave_platform, buildType, testType, test)
                                # have to check that custom_builder is not already present
                                if custom_builder in (builderNames) and custom_builder not in testBuilders:
                                    testBuilders.extend([custom_builder])

                    # we do all but debug win32 over on test masters so have to check the 
                    # unittestPrettyNames platforms for local builder master unittests
                    if unittestPrettyNames and unittestPrettyNames.has_key(platform):
                         for test in tests:
                             debug_custom_builder = "%s %s" % (unittestPrettyNames[platform], test)
                             if debug_custom_builder in (builderNames) and debug_custom_builder not in testBuilders:
                                 testBuilders.extend([debug_custom_builder])

        if testType == "talos":
            for platform in platforms:
                for test in tests:
                    for slave_platform in prettyNames[platform]:
                        custom_builder = "%s tryserver %s %s" % (slave_platform, testType, test)
                        if custom_builder in (builderNames) and custom_builder not in testBuilders:
                            testBuilders.extend([custom_builder])

    return testBuilders

def TryParser(message, builderNames, prettyNames, unittestPrettyNames=None, unittestSuites=None, talosSuites=None):

    parser = argparse.ArgumentParser(description='Pass in a commit message and a list \
                                     and tryParse populates the list with the builderNames\
                                     that need schedulers.')

    parser.add_argument('--do-everything', '-a',
                        action='store_true',
                        dest='do_everything',
                        help='m-c override to do all builds, tests, talos just like a trunk push')
    parser.add_argument('--build', '-b',
                        default='do',
                        dest='build',
                        help='accepts the build types requested')
    parser.add_argument('--platform', '-p',
                        default='all',
                        dest='user_platforms',
                        help='provide a list of platforms desired, or specify none (default is all)')
    parser.add_argument('--unittests', '-u',
                        default='all',
                        dest='test',
                        help='provide a list of unit tests, or specify all (default is None)')
    parser.add_argument('--talos', '-t',
                        default='none',
                        dest='talos',
                        help='provide a list of talos tests, or specify all (default is None)')

    (options, unknown_args) = parser.parse_known_args(processMessage(message))

    if options.do_everything:
        options.build = ['opt', 'debug']
        options.user_platforms = 'all'
        options.test = 'all'
        options.talos = 'all'

    # Build options include a possible override of 'all' to get a buildset that matches m-c
    if options.build == 'do' or options.build == 'od':
        options.build = ['opt', 'debug']
    elif options.build == 'd':
        options.build = ['debug']
    elif options.build == 'o':
        options.build = ['opt']
    else:
        # for any input other than do/od, d, o, all set to default
        options.build = ['opt','debug']

    if options.user_platforms == 'all' and prettyNames:
        options.user_platforms = prettyNames.keys()
    elif options.user_platforms != 'none':
        options.user_platforms = options.user_platforms.split(',')

    if options.test == 'all':
        options.test = unittestSuites
    elif options.test != 'none':
        options.test = expandTestSuites(options.test.split(','), unittestSuites)

    if talosSuites:
      if options.talos == 'all':
          options.talos = talosSuites
      elif options.talos != 'none':
          options.talos = options.talos.split(',')

    # List for the custom builder names that match prettyNames passed in from misc.py
    customBuilderNames = []
    if options.user_platforms:
        customBuilderNames = getPlatformBuilders(options.user_platforms, builderNames, options.build, prettyNames)

        if options.test != 'none' and unittestSuites:
            customBuilderNames.extend(getTestBuilders(options.user_platforms, "test", options.test, 
                                      builderNames, options.build, prettyNames, unittestPrettyNames))
        if options.talos != 'none' and talosSuites is not None:
            customBuilderNames.extend(getTestBuilders(options.user_platforms, "talos", options.talos, builderNames, 
                                      options.build, prettyNames, None))

    return customBuilderNames