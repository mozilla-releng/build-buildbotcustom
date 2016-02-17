from buildbotcustom.try_parser import TryParser, processMessage
import unittest


def base_platform(platform):
    return platform.replace(' try-nondefault', '')

###### TEST CASES #####

BUILDER_PRETTY_NAMES = {'macosx64': 'OS X 10.6.2 try build',
                        'macosx64-debug': 'OS X 10.6.2 try leak test build',
                        'macosx-debug': 'OS X 10.5.2 try leak test build',
                        'win32': 'WINNT 5.2 try build',
                        'win32-debug': 'WINNT 5.2 try leak test build',
                        'win64': 'WINNT 6.1 try try-nondefault build',
                        'linux64': 'Linux x86-64 try build',
                        'linux64-debug': 'Linux x86-64 try leak test build',
                        'linux64-valgrind': 'Linux x86-64 try valgrind',
                        'linux': 'Linux try build',
                        'linux-debug': 'Linux try leak test build',
                        'android-r7': 'Android R7 try build',
                        'maemo5-gtk': 'Maemo 5 GTK try build',
                        }
# TODO -- need to check on how to separate out the two win32 prettynames
TESTER_PRETTY_NAMES = {'macosx': ['Rev3 MacOSX Leopard 10.5.8'],
                       'macosx64': ['Rev3 MacOSX Snow Leopard 10.6.2',
                                    'Rev3 MacOSX Leopard 9.0 try-nondefault'],
                       'win32': ['Rev3 WINNT 5.1',
                                 'Windows XP 32-bit',
                                 'Rev3 WINNT 6.1'],
                       'linux64': ['Rev3 Fedora 12x64'],
                       'linux': ['Rev3 Fedora 12'],
                       'linux64_gecko': ['b2g_ubuntu64_vm'],
                       }
TESTER_PRETTY_TB_NAMES = {'linux': ['TB Rev3 Fedora 12']}
UNITTEST_PRETTY_NAMES = {'win32-debug': 'WINNT 5.2 try debug test'}
BUILDER_PRETTY_B2G_NAMES = {
    'emulator': 'b2g_try_emulator build',
    'emulator-debug': 'b2g_try_emulator-debug build'}

TALOS_SUITES = ['tp4', 'chrome']
UNITTEST_SUITES = ['reftest',
                   'crashtest',
                   'mochitest-1',
                   'mochitest-3',
                   'mochitest-browser-chrome',
                   'mochitest-devtools-chrome',
                   'mochitest-e10s-devtools-chrome-1',
                   'mochitest-other',
                   'gaia-js-integration-1',
                   'gaia-js-integration-2',
                   'gaia-ui-test-accessibility',
                   'gaia-ui-test-unit']
UNITTEST_SUITES_TB = ['xpcshell', 'mozmill']
MOBILE_UNITTEST_SUITES = ['reftest-1', 'reftest-3'] + UNITTEST_SUITES[1:]

VALID_UPN = ['WINNT 5.2 try debug test mochitest-1',
             'WINNT 5.2 try debug test mochitest-3',
             'WINNT 5.2 try debug test mochitest-browser-chrome',
             'WINNT 5.2 try debug test mochitest-e10s-devtools-chrome-1',
             'WINNT 5.2 try debug test mochitest-other',
             'WINNT 5.2 try debug test reftest',
             'WINNT 5.2 try debug test crashtest']
VALID_REFTEST_NAMES = ['WINNT 5.2 try debug test reftest',
                       'WINNT 5.2 try debug test reftest-1',
                       'WINNT 5.2 try debug test reftest-3']
VALID_BUILDER_NAMES = [base_platform(b)
                       for b in BUILDER_PRETTY_NAMES.values()]
VALID_TESTER_NAMES = ['Rev3 Fedora 12 try opt test mochitest-1',
                      'Rev3 Fedora 12 try opt test mochitest-browser-chrome',
                      'Rev3 Fedora 12 try opt test mochitest-other',
                      'Rev3 Fedora 12 try opt test crashtest',
                      'Rev3 Fedora 12 try debug test mochitest-1',
                      'Rev3 Fedora 12 try debug test mochitest-browser-chrome',
                      'Rev3 Fedora 12 try debug test mochitest-other',
                      'Rev3 WINNT 5.1 try opt test reftest',
                      'Rev3 WINNT 5.1 try opt test crashtest',
                      'Rev3 WINNT 6.1 try opt test crashtest',
                      'Rev3 WINNT 6.1 try debug test crashtest',
                      'Windows XP 32-bit try debug test crashtest',
                      'Rev3 WINNT 6.1 try debug test mochitest-browser-chrome',
                      'Rev3 WINNT 6.1 try debug test mochitest-other',
                      'Rev3 WINNT 6.1 try debug test mochitest-3',
                      'Rev3 MacOSX Snow Leopard 10.6.2 try debug test crashtest',
                      'Rev3 MacOSX Leopard 9.0 try debug test crashtest',
                      'Rev3 MacOSX Leopard 9.0 try talos tp4',
                      'Rev3 WINNT 5.1 try talos chrome',
                      'Rev3 WINNT 6.1 try talos tp4',
                      'Rev3 WINNT 5.1 try talos tp4',
                      'Rev3 WINNT 6.1 try talos chrome',
                      'b2g_ubuntu64_vm try opt test gaia-js-integration-1',
                      'b2g_ubuntu64_vm try opt test gaia-js-integration-2',
                      'b2g_ubuntu64_vm try opt test gaia-ui-test-unit',
                      'b2g_ubuntu64_vm try opt test gaia-ui-test-accessibility']
VALID_TESTER_TB_NAMES = ['TB Rev3 Fedora 12 try-comm-central opt test mozmill',
                         'TB Rev3 Fedora 12 try-comm-central opt test xpcshell']
VALID_BUILDER_B2G_NAMES = ['b2g_try_emulator-debug build',
                           'b2g_try_emulator build']


def dictslice(d, keys, default=None):
    if hasattr(keys, '__call__'):
        return dict([(k, v) for k, v in d.items() if keys(k)])
    else:
        return dict([(k, d.get(k, default)) for k in keys])


class TestTryParser(unittest.TestCase):

    def setUp(self):
        builders = self.filterTesters(['win32'])
        builders = [t for t in builders if 'crashtest' in t or 'reftest' in t]
        self.baselineBuilders = builders

    def removeNondefaults(self, builders, pretties):
        platform_names = pretties.values()
        if isinstance(platform_names[0], list):
            platform_names = reduce(lambda a, b: a + b, platform_names)
        nondefaults = [base_platform(n)
                       for n in platform_names
                       if 'try-nondefault' in n]
        nondefault_builders = set(
            [b for b in builders for nd in nondefaults if nd in b])
        return set(builders) - nondefault_builders

    def filterBuilders(self, platforms,
                       pretties=BUILDER_PRETTY_NAMES,
                       valid=VALID_BUILDER_NAMES):
        chosen = set()
        assert isinstance(platforms, list)
        for platform in platforms:
            arch = pretties[platform]
            assert not isinstance(arch, list)
            chosen.update([builder for builder in valid if arch in builder])
        return list(chosen)

    def filterTesters(self, platforms,
                      pretties=TESTER_PRETTY_NAMES,
                      valid=VALID_TESTER_NAMES):
        chosen = set()
        assert isinstance(platforms, list)
        for platform in platforms:
            slave_platforms = pretties.get(platform, [])
            assert isinstance(slave_platforms, list)
            for slave_platform in slave_platforms:
                base = base_platform(slave_platform)
                chosen.update(
                    [builder for builder in valid if base in builder])
        return list(chosen)

    def test_BlankMessage(self):
        # Should get empty set with blank input
        tm = ""
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        self.assertEqual(sorted(self.customBuilders), [])

    def test_JunkMessageBuilders(self):
        # Should get default set with junk input
        tm = "try: junk"
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = self.removeNondefaults(
            VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_JunkMessageTesters(self):
        # Should get default set with junk input to the test masters
        tm = "try: junk"
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [b for b in VALID_TESTER_NAMES if 'talos' not in b]
        builders = self.removeNondefaults(builders, TESTER_PRETTY_NAMES)
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_JunkBuildMessage(self):
        # Should get default set with junk input for --build
        tm = "try: -b k -p linux"
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = dictslice(
            BUILDER_PRETTY_NAMES, ['linux', 'linux-debug']).values()
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_DebugOnlyBuild(self):
        tm = "try: -b d -p linux64,linux"
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = [BUILDER_PRETTY_NAMES[p] for p in [
            'linux64-debug', 'linux-debug']]
        self.assertEquals(sorted(self.customBuilders), sorted(builders))

    def test_OptOnlyBuild(self):
        tm = "try: -b o -p macosx64,linux"
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = [BUILDER_PRETTY_NAMES[p] for p in ['macosx64', 'linux']]
        self.assertEquals(sorted(self.customBuilders), sorted(builders))

    def test_BothBuildTypes(self):
        # User can send 'do' or 'od' for both
        tm = ['try: -b od -p win32', 'try: -b do -p win32']
        for m in tm:
            self.customBuilders = TryParser(
                m, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
            builders = [BUILDER_PRETTY_NAMES[p] for p in [
                'win32', 'win32-debug']]
            self.assertEquals(sorted(self.customBuilders), sorted(builders))

    def test_SpecificPlatform(self):
        # Testing a specific platform, eg: mac only
        # should specify macosx and macosx64 to get opt and debug
        tm = 'try: -b od -p macosx64,macosx'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = [BUILDER_PRETTY_NAMES[p] for p in ['macosx64',
                                                      'macosx64-debug', 'macosx-debug']]
        self.assertEquals(sorted(self.customBuilders), sorted(builders))

    def test_B2GPlatform(self):
        tm = 'try: -b od -p emulator'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_B2G_NAMES, BUILDER_PRETTY_B2G_NAMES)
        builders = self.filterBuilders(
            ['emulator', 'emulator-debug'],
            pretties=BUILDER_PRETTY_B2G_NAMES,
            valid=VALID_BUILDER_B2G_NAMES)
        self.assertEquals(sorted(self.customBuilders), sorted(builders))
        tm = 'try: -b o -p emulator'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_B2G_NAMES, BUILDER_PRETTY_B2G_NAMES)
        builders = ['b2g_try_emulator build']
        builders = self.filterBuilders(['emulator'],
                                       pretties=BUILDER_PRETTY_B2G_NAMES,
                                       valid=VALID_BUILDER_B2G_NAMES)
        self.assertEquals(sorted(self.customBuilders), sorted(builders))

    def test_FullPlatformsBoth(self):
        tm = 'try: -b od -p full'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = VALID_BUILDER_NAMES
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_FullPlatformsOpt(self):
        tm = 'try: -b o -p full'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = dictslice(
            BUILDER_PRETTY_NAMES, lambda p: 'debug' not in p).values()
        builders = [base_platform(b) for b in builders]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_FullPlatformsDebug(self):
        tm = 'try: -b d -p full'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = dictslice(
            BUILDER_PRETTY_NAMES, lambda p: 'debug' in p).values()
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_AllPlatformsBoth(self):
        tm = 'try: -b od -p all'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = [b for b in BUILDER_PRETTY_NAMES.values(
        ) if 'nondefault' not in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_AllPlatformsOpt(self):
        tm = 'try: -b o -p all'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = dictslice(
            BUILDER_PRETTY_NAMES, lambda p: 'debug' not in p).values()
        builders = [b for b in builders if 'nondefault' not in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_AllPlatformsDebug(self):
        tm = 'try: -b d -p all'
        self.customBuilders = TryParser(
            tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        builders = dictslice(
            BUILDER_PRETTY_NAMES, lambda p: 'debug' in p).values()
        builders = [b for b in builders if 'nondefault' not in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_ValgrindBuilds(self):
        tm = "try: -bo -p linux64-valgrind"
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES)
        self.assertEquals(self.customBuilders, ["Linux x86-64 try valgrind"])

    def test_NoNondefaultTests(self):
        tm = 'try: -b d -p macosx64 -u all'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = self.filterTesters(['macosx64'])
        builders = self.removeNondefaults(builders, TESTER_PRETTY_NAMES)
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_NondefaultsTest(self):
        tm = 'try: -b d -p macosx64 -u all[]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = self.filterTesters(['macosx64'])
        builders = [b for b in builders if 'talos' not in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_AllOnTestMaster(self):
        tm = 'try: -a'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [n for n in VALID_TESTER_NAMES if 'talos' not in n]
        builders = self.removeNondefaults(builders, TESTER_PRETTY_NAMES)
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_AllOnTestMasterCC(self):
        tm = 'try: -a'
        self.customBuilders = TryParser(tm, VALID_TESTER_TB_NAMES, TESTER_PRETTY_TB_NAMES, None, UNITTEST_SUITES_TB, None, "try-comm-central")
        builders = VALID_TESTER_TB_NAMES
        builders = self.removeNondefaults(builders, TESTER_PRETTY_TB_NAMES)
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_MochitestAliasesOnBuilderMaster(self):
        tm = 'try: -b od -p win32 -u mochitests'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, BUILDER_PRETTY_NAMES, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = [BUILDER_PRETTY_NAMES[p] for p in ['win32', 'win32-debug']]
        builders += [t for t in VALID_UPN if 'mochitest' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))
        tm = 'try: -b od -p win32 -u mochitest-bc'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, BUILDER_PRETTY_NAMES, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = [BUILDER_PRETTY_NAMES[p] for p in ['win32', 'win32-debug']]
        builders += [t for t in VALID_UPN if 'mochitest-browser-chrome' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))
        tm = 'try: -b od -p win32 -u mochitest-o'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, BUILDER_PRETTY_NAMES, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = [BUILDER_PRETTY_NAMES[p] for p in ['win32', 'win32-debug']]
        builders += [t for t in VALID_UPN if 'mochitest-other' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_GijAliasOnTestMaster(self):
        tm = 'try: -b od -p all -u gaia-js-integration'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [t for t in VALID_TESTER_NAMES if 'gaia-js-integration' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_GipAliasOnTestMaster(self):
        tm = 'try: -b od -p all -u gaia-ui-test'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [t for t in VALID_TESTER_NAMES if 'gaia-ui-test' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_MochitestAliasesOnTestMaster(self):
        tm = 'try: -b od -p all -u mochitests'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [t for t in VALID_TESTER_NAMES if 'mochitest' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))
        tm = 'try: -b od -p win32 -u mochitest-bc'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [t for t in self.filterTesters(['win32'])
                    if 'mochitest-browser-chrome' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))
        tm = 'try: -b od -p win32 -u mochitest-o'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [t for t in self.filterTesters(['win32'])
                    if 'mochitest-other' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_MochitestAliasesOnTestMasterDebugOnly(self):
        tm = 'try: -b d -p all -u mochitests'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [t for t in VALID_TESTER_NAMES if 'mochitest' in t and 'debug' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_BuildMasterDebugWin32Tests(self):
        tm = 'try: -b d -p win32 -u mochitests'
        # test in the getBuilders (for local builder_master unittests)
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, {}, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = self.filterBuilders(['win32-debug'],
                                       valid=VALID_BUILDER_NAMES + VALID_UPN,
                                       pretties=UNITTEST_PRETTY_NAMES)
        builders = [t for t in builders if 'mochitest' in t and 'debug' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_ReftestAliases(self):
        tm = 'try: -b d -p win32 -u reftests'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, {}, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = self.filterBuilders(['win32-debug'],
                                       valid=VALID_BUILDER_NAMES + VALID_UPN,
                                       pretties=UNITTEST_PRETTY_NAMES)
        builders = [t for t in builders if 'reftest' in t]
        self.assertEquals(sorted(self.customBuilders), sorted(builders))
        tm = 'try: -b d -p win32 -u reftest'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, {}, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        self.assertEquals(sorted(self.customBuilders), sorted(builders))

    def test_DevtoolsE10sAliases(self):
        tm = 'try: -b d -p win32 -u mochitest-e10s-devtools-chrome'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, {}, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = self.filterBuilders(['win32-debug'],
                                       valid=VALID_BUILDER_NAMES + VALID_UPN,
                                       pretties=UNITTEST_PRETTY_NAMES)
        builders = [t for t in builders if 'mochitest-e10s-devtools-chrome' in t]
        self.assertEquals(sorted(self.customBuilders), sorted(builders))
        self.assertEquals(len(self.customBuilders), 1)

    def test_ReftestMobileAliases(self):
        tm = 'try: -b d -p win32 -u reftests'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_REFTEST_NAMES, {}, UNITTEST_PRETTY_NAMES, MOBILE_UNITTEST_SUITES)
        builders = self.filterBuilders(['win32-debug'],
                                       valid=VALID_BUILDER_NAMES +
                                       VALID_REFTEST_NAMES,
                                       pretties=UNITTEST_PRETTY_NAMES)
        # MOBILE_UNITTEST_SUITES only has 'reftest-1' and 'reftest-3', not
        # 'reftest', and the builder names are constructed through string
        # concatenation, so there's no straightforward way to exclude the plain
        # 'reftest' element of VALID_REFTEST_NAMES. I'll sidestep the issue for
        # now by only accepting the substring 'reftest-'.
        builders = [t for t in builders if 'reftest-' in t]
        self.assertEquals(sorted(self.customBuilders), sorted(builders))
        tm = 'try: -b d -p win32 -u reftest'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_REFTEST_NAMES, {}, UNITTEST_PRETTY_NAMES, MOBILE_UNITTEST_SUITES)
        self.assertEquals(sorted(self.customBuilders), sorted(builders))

    def test_SelectTests(self):
        tm = 'try: -b od -p win32 -u crashtest,mochitest-other'
        # test in the getBuilders (for local builder_master unittests)
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, BUILDER_PRETTY_NAMES, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = [BUILDER_PRETTY_NAMES['win32'],
                    BUILDER_PRETTY_NAMES['win32-debug']]
        testers = self.filterBuilders(['win32-debug'],
                                      valid=VALID_BUILDER_NAMES + VALID_UPN,
                                      pretties=UNITTEST_PRETTY_NAMES)
        builders += [
            t for t in testers if 'crashtest' in t or 'mochitest-other' in t]

        self.assertEqual(sorted(self.customBuilders), sorted(builders))
        # test in the getTestBuilders (for local builder_master unittests)
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = self.filterTesters(['win32'])
        builders = [
            t for t in builders if 'crashtest' in t or 'mochitest-other' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def testAllTestsWithSets(self):
        tm = 'try: -b o -p android-x86 -u all -t none'
        builders_with_sets = {'xpcshell': 'androidx86-set-4'}
        selected_builders = TryParser(tm,
                                      ['Android 4.2 x86 Emulator try opt test androidx86-set-4'],
                                      {
                                          'android-x86': 'Android 4.2 x86 Emulator',
                                      },
                                      None,
                                      ['androidx86-set-4'],
                                      buildersWithSetsMap=builders_with_sets)
        self.assertEqual(selected_builders,
                         ['Android 4.2 x86 Emulator try opt test androidx86-set-4'])

    def test_NoTests(self):
        tm = 'try: -b od -p linux,win32 -u none'
        # test in getBuilders
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = self.filterBuilders(
            ['linux', 'linux-debug', 'win32', 'win32-debug'])
        self.assertEqual(sorted(self.customBuilders), sorted(builders))
        # test in getTestBuilders
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = []
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_AllTalos(self):
        # should get all unittests too since that's the default set
        tm = 'try: -b od -t all'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES, TALOS_SUITES)
        builders = self.removeNondefaults(
            VALID_TESTER_NAMES, TESTER_PRETTY_NAMES)
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_SelecTalos(self):
        tm = 'try: -b od -p win32 -t tp4'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, None, TALOS_SUITES)
        builders = self.filterTesters(['win32'])
        builders = [t for t in builders if 'tp4' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_SelecTalosWithNoTalosPlatforms(self):
        tm = 'try: -b od -p win32,android-r7 -t tp4'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, None, TALOS_SUITES)
        builders = self.filterTesters(['win32', 'android-r7'])
        builders = [t for t in builders if 'tp4' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_NoTalos(self):
        tm = 'try: -b od -p linux,win32 -t none'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES, TESTER_PRETTY_NAMES, None, None, TALOS_SUITES)
        builders = []
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_DebugWin32OnTestMaster(self):
        tm = 'try: -b do -p win32 -u crashtest'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = self.filterTesters(['win32'])
        builders = [t for t in builders if 'crashtest' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_RestrictionBaseline(self):
        # This isn't really a test of anything. It's mostly to make reading the
        # following tests easier by giving the full set of possible builds
        # without any filtering.
        tm = 'try: -b do -p win32 -u crashtest,reftest'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = self.filterTesters(['win32'])
        builders = [t for t in builders if 'crashtest' in t or 'reftest' in t]
        self.assertEqual(sorted(self.baselineBuilders), sorted(builders))
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_Include5_1(self):
        tm = 'try: -b do -p win32 -u crashtest[5.1]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [b for b in self.baselineBuilders if 'crashtest' in b and '5.1' in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_IncludeExclude(self):
        tm = 'try: -b do -p win32 -u crashtest[-5.1,debug]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [b for b in self.baselineBuilders
                    if 'crashtest' in b
                          and '5.1' not in b
                          and 'debug' in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_IncludeExcludeEmpty(self):
        tm = 'try: -b do -p win32 -u crashtest[-5.1,5.1]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = []
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_IncludeDummyExclude(self):
        tm = 'try: -b do -p win32 -u crashtest[5.1,-notfound]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [b for b in self.baselineBuilders
                    if 'crashtest' in b
                          and '5.1' in b
                          and 'notfound' not in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_Exclude5_1(self):
        tm = 'try: -b do -p win32 -u crashtest[-5.1]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [b for b in self.baselineBuilders
                    if 'crashtest' in b
                          and '5.1' not in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_MultipleInclusions(self):
        tm = 'try: -b do -p win32 -u all[5.1,crash]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [
            b for b in self.baselineBuilders if '5.1' in b or 'crash' in b]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_bug875252(self):
        tm = 'try: -b do -p win32 -u crashtest[5.1,Windows XP]'
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [b for b in self.baselineBuilders
                    if 'crashtest' in b
                    and ('5.1' in b or 'Windows XP' in b)]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_HiddenCharactersAndOldSyntax(self):
        tm = 'attributes\ntry: -b o -p linux64 -m none -u reftest -t none'
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [BUILDER_PRETTY_NAMES['linux64']]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def test_NoBuildTypeSelected(self):
        tm = 'try: -m none -u crashtest -p win32'
        # should get both build types for the selected platform
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES, BUILDER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = [BUILDER_PRETTY_NAMES['win32'],
                    BUILDER_PRETTY_NAMES['win32-debug']]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

        # should get debug win32 in the builder_master test builders
        self.customBuilders = TryParser(tm, VALID_BUILDER_NAMES + VALID_UPN, {}, UNITTEST_PRETTY_NAMES, UNITTEST_SUITES)
        builders = self.filterBuilders(['win32-debug'],
                                       pretties=UNITTEST_PRETTY_NAMES,
                                       valid=VALID_BUILDER_NAMES + VALID_UPN)
        builders = [t for t in builders if 'crashtest' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

        # should get both build types in test_builders
        self.customBuilders = TryParser(tm, VALID_TESTER_NAMES, TESTER_PRETTY_NAMES, None, UNITTEST_SUITES)
        builders = self.filterTesters(['win32', 'win32-debug'])
        builders = [t for t in builders if 'crashtest' in t]
        self.assertEqual(sorted(self.customBuilders), sorted(builders))

    def _testNewLineProcessMessage(self, message, value=None):
        if not value:
            value = ['-a', '-b', '-c']
        self.assertEqual(processMessage(message), value)

    def test_SingleLine(self):
        self._testNewLineProcessMessage("""try: -a -b -c""")

    def test_SingleLineSpace(self):
        self._testNewLineProcessMessage("""try: -a -b -c """)

    def test_CommentSingleLine(self):
        self._testNewLineProcessMessage("""blah blah try: -a -b -c""")

    def test_CommentSingleLineSpace(self):
        self._testNewLineProcessMessage("""blah blah try: -a -b -c """)

    def test_FirstLineNewLine(self):
        self._testNewLineProcessMessage("""try: -a -b -c
some other comment
lines
blah""")

    def test_FirstLineSpaceNewLine(self):
        self._testNewLineProcessMessage("""try: -a -b -c
some other comment
lines
blah""")

    def test_CommentFirstLineNewLine(self):
        self._testNewLineProcessMessage("""blah blah try: -a -b -c
some other comment
lines
blah""")

    def test_CommentFirstLineSpaceNewLine(self):
        self._testNewLineProcessMessage("""blah blah try: -a -b -c
some other comment
lines
blah""")

    def test_MiddleLineNewLine(self):
        self._testNewLineProcessMessage("""blah blah
try: -a -b -c
some other comment
lines
blah""")

    def test_MiddleLineSpaceNewLine(self):
        self._testNewLineProcessMessage("""blah blah
try: -a -b -c
some other comment
lines
blah""")

    def test_CommentMiddleLineNewLine(self):
        self._testNewLineProcessMessage("""blah blah
blah blah try: -a -b -c
some other comment
lines
blah""")

    def test_CommentMiddleLineSpaceNewLine(self):
        self._testNewLineProcessMessage("""blah blah
blah blah try: -a -b -c
some other comment
lines
blah""")

    def test_LastLine(self):
        self._testNewLineProcessMessage("""blah blah
some other comment
lines
try: -a -b -c""")

    def test_LastLineSpace(self):
        self._testNewLineProcessMessage("""blah blah
some other comment
lines
try: -a -b -c """)

    def test_CommentLastLine(self):
        self._testNewLineProcessMessage("""blah blah
some other comment
lines
blah blah try: -a -b -c""")

    def test_CommentLastLineSpace(self):
        self._testNewLineProcessMessage("""blah blah
some other comment
lines
blah blah try: -a -b -c """)

    def test_DuplicateTryLines(self):
        self._testNewLineProcessMessage("""try: -a -b -c
try: -this -should -be -ignored""")

    def test_IgnoreTryColonNoSpace(self):
        self._testNewLineProcessMessage("""Should ignore this try:
try: -a -b -c""")


if __name__ == '__main__':
    unittest.main()
