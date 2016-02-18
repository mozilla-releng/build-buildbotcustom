# Mozilla Try Parser
# Contributor(s):
#   Lukas Blakk <lsblakk@mozilla.com>

import argparse
import re

from twisted.python import log

'''Given a list of arguments from commit message or info file
   returns only those builder names that should be built.'''


def testSuiteMatches(v, u):
    '''Check whether test suite v matches a user-requested test suite spec u'''
    if u in ('mochitests', 'mochitest'):
        return v.startswith('mochitest')
    elif u in ('jittests', 'jittest'):
        return v.startswith('jittest')
    elif u == 'mochitest-debug':
        return v.startswith('mochitest-debug-')
    elif u in ('mochitest-o'):
        return v in ['mochitest-other', 'mochitest-a11y', 'mochitest-chrome']
    elif u == 'xpcshell':
        return v.startswith('xpcshell')
    elif u == 'robocop':
        return v.startswith(u)
    elif u == 'mochitest-dt':
        return v.startswith('mochitest-devtools-chrome')
    elif u == 'mochitest-e10s-devtools-chrome' or u == 'mochitest-e10s-dt':
        return v.startswith('mochitest-e10s-devtools-chrome')
    elif u == 'mochitest-gl':
        return v.startswith('mochitest-gl')
    elif u.startswith('mochitest-dt'):
        # mochitest-dt1 and mochitest-dt-1 should run
        # mochitest-devtools-chrome-1
        return v == re.sub(r"dt-?", "devtools-chrome-", u)
    elif u in ('mochitest-bc', 'mochitest-browser'):
        return v.startswith('mochitest-browser-chrome')
    elif u.startswith('mochitest-bc'):
        # mochitest-bc1 and mochitest-bc-1 should run mochitest-browser-chrome-1
        return v == re.sub(r"bc-?", "browser-chrome-", u)
    elif u in ('mochitest-e10s-bc', 'mochitest-e10s-browser'):
        return v.startswith('mochitest-e10s-browser-chrome')
    elif u.startswith('mochitest-e10s-bc'):
        # mochitest-e10s-bc1 and mochitest-e10s-bc-1 should run mochitest-e10s-browser-chrome-1
        return v == re.sub(r"bc-?", "browser-chrome-", u)
    elif u in ('crashtests', 'crashtest'):
        return v.startswith('crashtest')
    elif u in ('reftests', 'reftest'):
        return v.startswith('reftest') or v.startswith('plain-reftest')
    elif u in ('web-platform-tests', 'web-platform-test'):
        return v.startswith("web-platform-tests")
    elif u == 'e10s':
        return 'e10s' in v
    elif u == 'gaia-js-integration':
        return 'gaia-js-integration' in v
    elif u == 'gaia-ui-test':
        return 'gaia-ui-test' in v
    elif u == 'all':
        return True
    else:
        # validate other test names
        return u == v


def expandTestSuites(user_suites, valid_suites):
    '''Grab out all of the test suites from valid_suites that match something
       requested by the user'''
    return [v for v in valid_suites for u in user_suites if testSuiteMatches(v, u)]


def processMessage(message):
    for line in message.split('\n'):
        match = re.search('try: ', str(line))
        if match:
            line = line.strip().split('try: ', 1)
            # Allow spaces inside of [filter expressions]
            return re.findall(r'(?:\[.*?\]|\S)+', line[1])
    return None


def expandPlatforms(user_platforms, buildTypes):
    platforms = set()
    if 'opt' in buildTypes:
        platforms.update(user_platforms)
    if 'debug' in buildTypes:
        platforms.update([p + '-debug' for p in user_platforms])
    return platforms


def basePlatform(platform):
    '''Platform name without any 'try-nondefault' markers, whether at the
    beginning or in the middle of the string'''
    return platform.replace(' try-nondefault', '').replace('try-nondefault ', '')


def getPlatformBuilders(user_platforms, builderNames, buildTypes, prettyNames):
    '''Return builder names that are found in both prettyNames[p] for some
       (expanded) platform p, and in builderNames'''

    # When prettyNames contains list values rather than simple strings, it
    # means that we're processing the argument for selecting test suites, so do
    # not return any build builders.
    if prettyNames and isinstance(prettyNames.values()[0], list):
        return []

    platforms = expandPlatforms(user_platforms, buildTypes)
    builders = [basePlatform(prettyNames[p])
                for p in platforms.intersection(prettyNames)]
    return list(set(builders).intersection(builderNames))


def passesFilter(testFilters, test, pretty, isDefault):
    if test not in testFilters:
        # No filter requested for test, so accept all defaults
        return isDefault

    # If a filter *has* been set, then ignore the try-nondefault flag;
    # everything is eligible for selection

    # filters is a set of inclusion and exclusion rules. Exclusions begin with
    # '-'. To be accepted, a pretty name must match at least one inclusion and
    # no exclusion -- unless no inclusions are given, in which case the pretty
    # name has to just not match any exclusions.
    #
    #   all[a] means "anything that matches a"
    #   all[a,-x] means "anything that matches a and not x"
    #   all[a,b,-x] means "anything that matches a or b but does not match x"
    #   all[-x] means "anything that does not match x"
    #   all[-x,-y] means "anything that matches neither x nor y"
    sawInclusion = False
    matchedInclusion = False
    for f in testFilters[test]:
        if f.startswith('-'):
            if f[1:] in pretty:
                return False
        else:
            sawInclusion = True
            if f in pretty:
                matchedInclusion = True

    return matchedInclusion or not sawInclusion


def getTestBuilders(
    platforms, testType, tests, testFilters, builderNames, buildTypes, buildbotBranch,
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
                            pretties = [pretties]
                        for pretty in pretties:
                            base_pretty = basePlatform(pretty)
                            custom_builder = "%s %s %s %s %s" % (base_pretty, buildbotBranch, buildType, testType, test)
                            if passesFilter(testFilters, test, custom_builder, base_pretty == pretty):
                                testBuilders.add(custom_builder)

        # we do all but debug win32 over on test masters so have to check the
        # unittestPrettyNames platforms for local builder master unittests
        for platform in builder_test_platforms.intersection(unittestPrettyNames or {}):
            assert platform.endswith('-debug')
            for test in tests:
                pretty = unittestPrettyNames[platform]
                base_pretty = basePlatform(pretty)
                debug_custom_builder = "%s %s" % (base_pretty, test)
                if passesFilter(testFilters, test, debug_custom_builder, base_pretty == pretty):
                    testBuilders.add(debug_custom_builder)

    if testType == "talos":
        for platform in set(platforms).intersection(prettyNames):
            # check whether we do talos for this platform
            for slave_platform in prettyNames[platform]:
                base_slave_platform = basePlatform(slave_platform)
                for test in tests:
                    custom_builder = "%s %s talos %s" % (
                        base_slave_platform, buildbotBranch, test)
                    if passesFilter(testFilters, test, custom_builder, base_slave_platform == slave_platform):
                        testBuilders.add(custom_builder)

    return list(testBuilders.intersection(builderNames))


def parseTestOptions(s, testSuites):
    '''parse a comma-separated list of tests, each optionally followed by a
    comma-separated list of restrictions enclosed in square brackets

    Examples:
      none - returns the empty list

      all - returns all known test suites

      all[moch] - returns all known tests suites with 'moch' in their prettyNames

      test1,test2[moch,ref],test3 - restrictions can be specific to a test suite

      test[-moch,ref] - a preceding '-' character means to accept any test whose
        prettyName does NOT contain the following substring

      test[a,b,-x,-y] - the '-' character binds to only the next option, so this is
        "any builder containing either the substring a or the substring b, excluding
        those that contain either x or y."

      test[-x] - If no positive substrings are given, anything matches except builders
        whose prettyNames contain x.
      '''

    if s == 'none':
        return [], {}

    # Handle nested commas by extracting out all restrictions and replacing
    # them with a numeric id, saving the list of restrictions in an array
    # indexed by those ids. This allows a simple split on comma to find the
    # list of test suites requested. Example:
    #
    #    "mochitests[a,b],test2,mochitest-1[c]"
    #
    #  gets turned into
    #
    #    "mochitests[0],test2,mochitest-1[1]" plus a side table
    #      0: [ 'a', 'b' ]
    #      1: [ 'c' ]
    #
    #  gets split into
    #
    #    [ 'mochitests[0]', 'test2', 'mochitest-1[1]' ]
    #
    #  which is scanned to produce a final set of tests requested:
    #
    #    [ 'mochitest-1', 'test2', 'mochitest-2', ... ]
    #
    # and a mapping table from each test to the set of restrictions:
    #
    #    mochitest-1: [ 'c' ]
    #    test2: []
    #    mochitest-2: [ 'a', 'b' ]
    #    ...
    #
    # These will be tested against tests' prettyNames in passesFilter().
    #
    # Note that if the same test shows up multiple times in the list (eg
    # mochitest-1 in the example above), the last set of restrictions for that
    # test will override any previous ones. (Unioning the restrictions is less
    # likely to be what the user intended, especially when exclusion-only
    # filters are involved.)
    #
    restrictions = []

    def grab_restrictions(m):
        n = len(restrictions)
        s = m.group(1)
        restrictions.append(s.split(','))
        return '[' + str(n) + ']'
    # Replace restrictions inside of square brackets with a numeric id, and
    # generate a side table mapping that numeric id to a list of restrictions
    s = re.sub(r'\[(.*?)\]', grab_restrictions, s)

    all_tests = set()
    restrictions_map = {}
    for t in s.split(','):
        # Grab out the stuff before and after the square brackets
        m = re.match(r'(.*?)(?:\[(\d+)\])?$', t)
        if not m:
            return []  # Bad syntax

        tests = expandTestSuites([m.group(1)], testSuites)
        if m.group(2):
            for test in tests:
                restrictions_map[test] = restrictions[int(m.group(2))]

        all_tests.update(tests)

    return list(all_tests), restrictions_map


def TryParser(
    message, builderNames, prettyNames, unittestPrettyNames=None, unittestSuites=None, talosSuites=None,
        buildbotBranch='try', buildersWithSetsMap=None):

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

    message = processMessage(message)
    if message is None:
        # no try syntax found, don't schedule anything
        return []

    (options, unknown_args) = parser.parse_known_args(message)

    # Build options include a possible override of 'all' to get a buildset
    # that matches m-c
    if options.build == 'do' or options.build == 'od':
        options.build = ['opt', 'debug']
    elif options.build == 'd':
        options.build = ['debug']
    elif options.build == 'o':
        options.build = ['opt']
    else:
        # for any input other than do/od, d, o, all set to default
        options.build = ['opt', 'debug']

    if buildersWithSetsMap and type(buildersWithSetsMap) is dict:
        # The TryChooser user has set a comma separated list of test suites
        # This platform has a dictionary that allows to match a test suite
        # to an actual builder (e.g. {"mochitest-1": "androidx86-set-1"}
        chosen_suites = options.test.split(',')
        new_choice = []
        for chosen_suite in chosen_suites:
            if chosen_suite == 'all':
                new_choice.append(chosen_suite)
                continue
            if buildersWithSetsMap.has_key(chosen_suite):
                if chosen_suite not in new_choice:
                    new_choice.append(buildersWithSetsMap[chosen_suite])
        options.test = ','.join(new_choice)

    if unittestSuites:
        all_platforms = prettyNames.keys()
    else:
        # for build builders (as opposed to test builders), check against the
        # prettyNames for -debug
        all_platforms = set()
        if 'debug' in options.build:
            all_platforms.update(
                [p for p in prettyNames.keys() if p.endswith('debug')])
        if 'opt' in options.build:
            all_platforms.update(
                [p for p in prettyNames.keys() if not p.endswith('debug')])

        # Strip off -debug. It gets tacked on in the getPlatformBuilders for
        # buildType == debug
        all_platforms = list(
            set([p.replace('-debug', '') for p in all_platforms]))

    # Platforms whose prettyNames all have 'try-nondefault' in them are not
    # included in -p all
    default_platforms = set()
    if unittestSuites or talosSuites:
        for p in all_platforms:
            default_platforms.update(
                [p for n in prettyNames[p] if 'try-nondefault' not in n])
    else:
        defaultPrettyNames = dict([(k, v)
                                   for k, v in prettyNames.iteritems()
                                   if 'try-nondefault' not in v])
        for p in all_platforms:
            if p in defaultPrettyNames:
                default_platforms.add(p)
            elif p + '-debug' in defaultPrettyNames:
                default_platforms.add(p)

    user_platforms = set()
    for platform in options.user_platforms.split(','):
        if platform == 'all':
            user_platforms.update(default_platforms)
        elif platform == 'full':
            user_platforms.update(all_platforms)
        else:
            user_platforms.add(platform)

    options.user_platforms = user_platforms

    testFilters = None
    if unittestSuites:
        options.test, testFilters = parseTestOptions(
            options.test, unittestSuites)

    talosTestFilters = None
    if talosSuites:
        options.talos, talosTestFilters = parseTestOptions(
            options.talos, talosSuites)

    # List for the custom builder names that match prettyNames passed in from
    # misc.py
    customBuilderNames = []
    if options.user_platforms:
        log.msg("TryChooser OPTIONS : MESSAGE %s : %s" % (options, message))
        customBuilderNames = getPlatformBuilders(
            options.user_platforms, builderNames, options.build, prettyNames)

        if options.test and unittestSuites:
            # get test builders for test_master first
            customBuilderNames.extend(
                getTestBuilders(
                    options.user_platforms, "test", options.test, testFilters,
                    builderNames, options.build, buildbotBranch, prettyNames, None))
            # then add any builder_master test builders
            if unittestPrettyNames:
                customBuilderNames.extend(
                    getTestBuilders(
                        options.user_platforms, "test", options.test, testFilters,
                        builderNames, options.build, buildbotBranch, {}, unittestPrettyNames))
        if options.talos and talosSuites:
            customBuilderNames.extend(
                getTestBuilders(
                    options.user_platforms, "talos", options.talos, talosTestFilters, builderNames,
                    options.build, buildbotBranch, prettyNames, None))

    return customBuilderNames
