# valid_builders.py

# A config file of what active try platforms & test/talos suites are available
# as well as a map of platforms -> prety names for creating and validating scheduler names

# TODO: File a bug for automating the generating of this file
PRETTY_NAMES = {
                'linux': 'Rev3 Fedora 12',
                'desktop_linux': 'Linux',
                'linux64': 'Rev3 Fedora 12x64',
                'desktop_linux64': 'Linux x86-64',
                'macosx': 'Rev3 MacOSX Leopard 10.5.8',
                'desktop_macosx': 'OS X 10.5.2',
                'macosx64': 'Rev3 MacOSX Snow Leopard 10.6.2',
                'desktop_macosx64': 'OS X 10.6.2',
                'win32': ['Rev3 WINNT 5.1', 'Rev3 WINNT 6.1'],
                'desktop_win32': 'WINNT 5.2',
                'win764': 'Rev3 WINNT 6.1 x64',
                'maemo5-gtk': 'Maemo 5 GTK',
                'maemo5-qt': 'Maemo 5 QT',
                'android-r7': 'Android R7',
                'mobile_linux': 'Linux Mobile Desktop',
                'mobile_win32': 'WINNT 5.2 Mobile Desktop',
                'mobile_macosx': 'OS X 10.5.2 Mobile Desktop',
                }
DESKTOP_PLATFORMS = ['linux','linux64','macosx','macosx64','win32']
MOBILE_PLATFORMS = ['android-r7', 'maemo5-gtk', 'maemo5-qt']
UNITTEST_SUITES = {
                  'mochitests': {
                                  'mochitest-1': 'mochitests-1/5',
                                  'mochitest-2': 'mochitests-2/5',
                                  'mochitest-3': 'mochitests-3/5',
                                  'mochitest-4': 'mochitests-4/5', 
                                  'mochitest-5': 'mochitests-5/5', 
                                  'mochitest-o': 'mochitest-other',
                                  },
                   'reftest': {},
                   'crashtest': {},
                   'xpcshell': {},
                   'jsreftest': {},
                   'opengl': {},
                   }
TALOS_SUITES = ['nochrome', 'dromaeo', 'a11y', 'svg', 'chrome', 'tp4', 'dirty', 'scroll', 'cold', 'v8']
