# Mozilla Try Parser
# Contributor(s):
#   Lukas Blakk <lsblakk@mozilla.com>

import argparse, re

from twisted.python import log

'''Given a list of arguments from commit message or info file
   returns only those builder names that should be built.'''

def testSuiteMatches(v, u):
    '''Check whether test suite v matches a user-requested test suite spec u'''
    if u in ('mochitests', 'mochitest'):
        return v.startswith('mochitest')
    elif u == 'mochitest-o':
        return re.search(u, v)
    elif u == 'mochitest-bc':
        return v.startswith('mochitest-browser-chrome')
    elif u in ('reftests', 'reftest'):
        return v.startswith('reftest')
    else:
        # validate other test names
        return u == v

def expandTestSuites(user_suites, valid_suites):
    '''Grab out all of the test suites from valid_suites that match something
       requested by the user'''
    return [ v for v in valid_suites for u in user_suites if testSuiteMatches(v, u) ]

def processMessage(message):
    for line in message.split('\n'):
        match = re.search('try: ',str(line))
        if match:
            line = line.strip().split('try: ', 1)
            line = line[1].split(' ')
            return line
    return [""]

def expandPlatforms(user_platforms, buildTypes):
    platforms = set()
    if 'opt' in buildTypes:
        platforms.update(user_platforms)
    if 'debug' in buildTypes:
        platforms.update([ "%s-debug" % p for p in user_platforms ])
    return platforms

def getPlatformBuilders(user_platforms, builderNames, buildTypes, prettyNames):
    '''Return builder names that are found in both prettyNames[p] for some
       (expanded) platform p, and in builderNames'''

    if user_platforms == 'none':
        return []

    # When prettyNames contains list values rather than simple strings, it
    # means that we're processing the argument for selecting test suites, so do
    # not return any build builders.
    if prettyNames and isinstance(prettyNames.values()[0], list):
        return []

    platforms = expandPlatforms(user_platforms, buildTypes)
    builders = [ prettyNames[p] for p in platforms.intersection(prettyNames) ]
    return list(set(builders).intersection(builderNames))

def getTestBuilders(platforms, testType, tests, builderNames, buildTypes, buildbotBranch,
                    prettyNames, unittestPrettyNames):
    if tests == 'none':
        return []

    testBuilders = set()
    # for all possible suites, add in the builderNames for that platform
    if testType == "test":
        builder_test_platforms = set()
        for buildType in buildTypes:
            for platform in platforms:
                # this is to catch debug unittests triggered on the build master
                # if the user asks for win32 with -b d
                if buildType == 'debug' and not platform.endswith('debug'):
                    builder_test_platforms.add('%s-debug' % platform)
                if platform in prettyNames:
                    for test in tests:
                        # check for list type to handle test_master builders
                        # where slave_platforms are used
                        pretties = prettyNames[platform]
                        if not isinstance(pretties, list):
                            pretties = [ pretties ]
                        for pretty in pretties:
                            custom_builder = "%s %s %s %s %s" % (pretty, buildbotBranch, buildType, testType, test)
                            testBuilders.add(custom_builder)

        # we do all but debug win32 over on test masters so have to check the 
        # unittestPrettyNames platforms for local builder master unittests
        for platform in builder_test_platforms.intersection(unittestPrettyNames or {}):
            assert platform.endswith('-debug')
            for test in tests:
                debug_custom_builder = "%s %s" % (unittestPrettyNames[platform], test)
                testBuilders.add(debug_custom_builder)

    if testType == "talos":
        for platform in set(platforms).intersection(prettyNames):
            # check whether we do talos for this platform
            for slave_platform in prettyNames[platform]:
                for test in tests:
                    custom_builder = "%s %s talos %s" % (slave_platform, buildbotBranch, test)
                    testBuilders.add(custom_builder)

    return list(testBuilders.intersection(builderNames))

def TryParser(message, builderNames, prettyNames, unittestPrettyNames=None, unittestSuites=None, talosSuites=None,
              buildbotBranch='try'):

    parser = argparse.ArgumentParser(description='Pass in a commit message and a list \
                                     and tryParse populates the list with the builderNames\
                                     that need schedulers.')

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
        # test builder pretty names don't have -debug in them, so all gets all prettyNames
        if options.test != 'none' and unittestSuites:
            options.user_platforms = prettyNames.keys()
        else:
            # for builders though, you need to check against the prettyNames for -debug
            options.user_platforms = []
            for buildType in options.build:
                for platform in prettyNames.keys():
                    if buildType == 'debug' and platform.endswith('debug'):
                        # append platform with the -debug stripped off
                        # it gets tacked on in the getPlatformBuilders for buildType == debug
                        options.user_platforms.append(platform.split('-')[0])
                    elif buildType == 'opt' and not platform.endswith('debug'):
                        options.user_platforms.append(platform)
    elif options.user_platforms != 'none':
        # ugly
        user_platforms = []
        for user_platform in options.user_platforms.split(','):
            user_platforms.append(user_platform)
        options.user_platforms = user_platforms

    if unittestSuites:
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
        log.msg("TryChooser OPTIONS : MESSAGE %s : %s" % (options, message))
        customBuilderNames = getPlatformBuilders(options.user_platforms, builderNames, options.build, prettyNames)

        if options.test != 'none' and unittestSuites:
            # get test builders for test_master first
            customBuilderNames.extend(getTestBuilders(options.user_platforms, "test", options.test, 
                                      builderNames, options.build, buildbotBranch, prettyNames, None))
            # then add any builder_master test builders
            if unittestPrettyNames:
                customBuilderNames.extend(getTestBuilders(options.user_platforms, "test", options.test, 
                                      builderNames, options.build, buildbotBranch, {}, unittestPrettyNames))
        if options.talos != 'none' and talosSuites is not None:
            customBuilderNames.extend(getTestBuilders(options.user_platforms, "talos", options.talos, builderNames, 
                                      options.build, buildbotBranch, prettyNames, None))

    return customBuilderNames
