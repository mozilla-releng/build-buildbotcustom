from buildbotcustom.try_parser import TryParser
import unittest
from valid_builders import DESKTOP_BUILDERS, MOBILE_BUILDERS, TEST_BUILDERS, TALOS_BUILDERS

###### TEST CASES #####

# ALL OVERRIDE
# test that when try comments contain --all, that an entire run of everything is generated
# this use case is to mimic an m-c run
MESSAGE_ALL_OVERRIDE = "try: '--build all"
RESULT_ALL_OVERRIDE = ['OS X 10.5.2 tryserver build', 'OS X 10.6.2 tryserver build', 'WINNT 5.2 tryserver build', 'Linux x86-64 tryserver build', 'Linux tryserver build', 'WINNT 5.2 tryserver leak test build', 'OS X 10.6.2 tryserver leak test build', 'Linux x86-64 tryserver leak test build', 'Linux tryserver leak test build', 'OS X 10.5.2 tryserver leak test build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-1/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-2/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-3/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-4/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-5/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitest-other', 'Rev3 WINNT 6.1 x64 tryserver opt test reftest', 'Rev3 WINNT 6.1 x64 tryserver opt test crashtest', 'Rev3 WINNT 6.1 x64 tryserver opt test xpcshell', 'Rev3 WINNT 6.1 x64 tryserver opt test jsreftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-1/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-2/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-3/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-4/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-5/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitest-other', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test reftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test crashtest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test xpcshell', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test jsreftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-1/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-2/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-3/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-4/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-5/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitest-other', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test reftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test crashtest', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test xpcshell', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test jsreftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-1/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-2/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-3/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-4/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-5/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitest-other', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test reftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test crashtest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test xpcshell', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test jsreftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-1/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-2/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-3/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-4/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-5/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitest-other', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test reftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test crashtest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test xpcshell', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test jsreftest', 'Rev3 WINNT 6.1 tryserver opt test mochitests-1/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-2/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-3/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-4/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-5/5', 'Rev3 WINNT 6.1 tryserver opt test mochitest-other', 'Rev3 WINNT 6.1 tryserver opt test reftest', 'Rev3 WINNT 6.1 tryserver opt test reftest-d2d', 'Rev3 WINNT 6.1 tryserver opt test crashtest', 'Rev3 WINNT 6.1 tryserver opt test xpcshell', 'Rev3 WINNT 6.1 tryserver opt test jsreftest', 'Rev3 Fedora 12x64 tryserver opt test mochitests-1/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-2/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-3/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-4/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-5/5', 'Rev3 Fedora 12x64 tryserver opt test mochitest-other', 'Rev3 Fedora 12x64 tryserver opt test reftest', 'Rev3 Fedora 12x64 tryserver opt test crashtest', 'Rev3 Fedora 12x64 tryserver opt test xpcshell', 'Rev3 Fedora 12x64 tryserver opt test jsreftest', 'Rev3 Fedora 12x64 tryserver debug test mochitests-1/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-2/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-3/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-4/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-5/5', 'Rev3 Fedora 12x64 tryserver debug test mochitest-other', 'Rev3 Fedora 12x64 tryserver debug test reftest', 'Rev3 Fedora 12x64 tryserver debug test crashtest', 'Rev3 Fedora 12x64 tryserver debug test xpcshell', 'Rev3 Fedora 12x64 tryserver debug test jsreftest', 'Rev3 Fedora 12 tryserver opt test mochitests-1/5', 'Rev3 Fedora 12 tryserver opt test mochitests-2/5', 'Rev3 Fedora 12 tryserver opt test mochitests-3/5', 'Rev3 Fedora 12 tryserver opt test mochitests-4/5', 'Rev3 Fedora 12 tryserver opt test mochitests-5/5', 'Rev3 Fedora 12 tryserver opt test mochitest-other', 'Rev3 Fedora 12 tryserver opt test reftest', 'Rev3 Fedora 12 tryserver opt test crashtest', 'Rev3 Fedora 12 tryserver opt test xpcshell', 'Rev3 Fedora 12 tryserver opt test jsreftest', 'Rev3 Fedora 12 tryserver debug test mochitests-1/5', 'Rev3 Fedora 12 tryserver debug test mochitests-2/5', 'Rev3 Fedora 12 tryserver debug test mochitests-3/5', 'Rev3 Fedora 12 tryserver debug test mochitests-4/5', 'Rev3 Fedora 12 tryserver debug test mochitests-5/5', 'Rev3 Fedora 12 tryserver debug test mochitest-other', 'Rev3 Fedora 12 tryserver debug test reftest', 'Rev3 Fedora 12 tryserver debug test crashtest', 'Rev3 Fedora 12 tryserver debug test xpcshell', 'Rev3 Fedora 12 tryserver debug test jsreftest', 'Rev3 WINNT 6.1 x64 tryserver talos nochrome', 'Rev3 WINNT 6.1 x64 tryserver talos dromaeo', 'Rev3 WINNT 6.1 x64 tryserver talos a11y', 'Rev3 WINNT 6.1 x64 tryserver talos svg', 'Rev3 WINNT 6.1 x64 tryserver talos chrome', 'Rev3 WINNT 6.1 x64 tryserver talos tp4', 'Rev3 WINNT 6.1 x64 tryserver talos dirty', 'Rev3 WINNT 6.1 x64 tryserver talos scroll', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos nochrome', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos cold', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos dromaeo', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos svg', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos chrome', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos tp4', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos dirty', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos scroll', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos nochrome', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos cold', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos dromaeo', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos svg', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos chrome', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos tp4', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos dirty', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos scroll', 'Rev3 WINNT 5.1 tryserver talos nochrome', 'Rev3 WINNT 5.1 tryserver talos dromaeo', 'Rev3 WINNT 5.1 tryserver talos a11y', 'Rev3 WINNT 5.1 tryserver talos svg', 'Rev3 WINNT 5.1 tryserver talos chrome', 'Rev3 WINNT 5.1 tryserver talos tp4', 'Rev3 WINNT 5.1 tryserver talos dirty', 'Rev3 WINNT 5.1 tryserver talos scroll', 'Rev3 WINNT 6.1 tryserver talos nochrome', 'Rev3 WINNT 6.1 tryserver talos dromaeo', 'Rev3 WINNT 6.1 tryserver talos a11y', 'Rev3 WINNT 6.1 tryserver talos svg', 'Rev3 WINNT 6.1 tryserver talos chrome', 'Rev3 WINNT 6.1 tryserver talos tp4', 'Rev3 WINNT 6.1 tryserver talos dirty', 'Rev3 WINNT 6.1 tryserver talos scroll', 'Rev3 Fedora 12x64 tryserver talos nochrome', 'Rev3 Fedora 12x64 tryserver talos cold', 'Rev3 Fedora 12x64 tryserver talos dromaeo', 'Rev3 Fedora 12x64 tryserver talos a11y', 'Rev3 Fedora 12x64 tryserver talos svg', 'Rev3 Fedora 12x64 tryserver talos chrome', 'Rev3 Fedora 12x64 tryserver talos tp4', 'Rev3 Fedora 12x64 tryserver talos dirty', 'Rev3 Fedora 12x64 tryserver talos scroll', 'Rev3 Fedora 12 tryserver talos nochrome', 'Rev3 Fedora 12 tryserver talos cold', 'Rev3 Fedora 12 tryserver talos dromaeo', 'Rev3 Fedora 12 tryserver talos a11y', 'Rev3 Fedora 12 tryserver talos svg', 'Rev3 Fedora 12 tryserver talos chrome', 'Rev3 Fedora 12 tryserver talos tp4', 'Rev3 Fedora 12 tryserver talos dirty', 'Rev3 Fedora 12 tryserver talos scroll', 'WINNT 5.2 tryserver debug test mochitests-1/5', 'WINNT 5.2 tryserver debug test mochitests-2/5', 'WINNT 5.2 tryserver debug test mochitests-3/5', 'WINNT 5.2 tryserver debug test mochitests-4/5', 'WINNT 5.2 tryserver debug test mochitests-5/5', 'WINNT 5.2 tryserver debug test mochitest-other', 'WINNT 5.2 tryserver debug test reftest', 'WINNT 5.2 tryserver debug test crashtest', 'WINNT 5.2 tryserver debug test xpcshell', 'WINNT 5.2 tryserver debug test jsreftest']

#  DEFAULT SET
# nothing in comments should give all available platforms opt & debug desktop builders, all mobile, all test, no talos
# only try: in comments same as above
MESSAGE_DEFAULT1 = ''
MESSAGE_DEFAULT2 = "junk"
RESULT_DEFAULT = ['OS X 10.5.2 tryserver build', 'OS X 10.6.2 tryserver build', 'WINNT 5.2 tryserver build', 'Linux x86-64 tryserver build', 'Linux tryserver build', 'WINNT 5.2 tryserver leak test build', 'OS X 10.6.2 tryserver leak test build', 'Linux x86-64 tryserver leak test build', 'Linux tryserver leak test build', 'OS X 10.5.2 tryserver leak test build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-1/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-2/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-3/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-4/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-5/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitest-other', 'Rev3 WINNT 6.1 x64 tryserver opt test reftest', 'Rev3 WINNT 6.1 x64 tryserver opt test crashtest', 'Rev3 WINNT 6.1 x64 tryserver opt test xpcshell', 'Rev3 WINNT 6.1 x64 tryserver opt test jsreftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-1/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-2/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-3/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-4/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-5/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitest-other', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test reftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test crashtest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test xpcshell', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test jsreftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-1/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-2/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-3/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-4/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitests-5/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test mochitest-other', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test reftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test crashtest', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test xpcshell', 'Rev3 MacOSX Leopard 10.5.8 tryserver debug test jsreftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-1/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-2/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-3/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-4/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-5/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitest-other', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test reftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test crashtest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test xpcshell', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test jsreftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-1/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-2/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-3/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-4/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitests-5/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test mochitest-other', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test reftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test crashtest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test xpcshell', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver debug test jsreftest', 'Rev3 WINNT 6.1 tryserver opt test mochitests-1/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-2/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-3/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-4/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-5/5', 'Rev3 WINNT 6.1 tryserver opt test mochitest-other', 'Rev3 WINNT 6.1 tryserver opt test reftest', 'Rev3 WINNT 6.1 tryserver opt test reftest-d2d', 'Rev3 WINNT 6.1 tryserver opt test crashtest', 'Rev3 WINNT 6.1 tryserver opt test xpcshell', 'Rev3 WINNT 6.1 tryserver opt test jsreftest', 'Rev3 Fedora 12x64 tryserver opt test mochitests-1/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-2/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-3/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-4/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-5/5', 'Rev3 Fedora 12x64 tryserver opt test mochitest-other', 'Rev3 Fedora 12x64 tryserver opt test reftest', 'Rev3 Fedora 12x64 tryserver opt test crashtest', 'Rev3 Fedora 12x64 tryserver opt test xpcshell', 'Rev3 Fedora 12x64 tryserver opt test jsreftest', 'Rev3 Fedora 12x64 tryserver debug test mochitests-1/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-2/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-3/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-4/5', 'Rev3 Fedora 12x64 tryserver debug test mochitests-5/5', 'Rev3 Fedora 12x64 tryserver debug test mochitest-other', 'Rev3 Fedora 12x64 tryserver debug test reftest', 'Rev3 Fedora 12x64 tryserver debug test crashtest', 'Rev3 Fedora 12x64 tryserver debug test xpcshell', 'Rev3 Fedora 12x64 tryserver debug test jsreftest', 'Rev3 Fedora 12 tryserver opt test mochitests-1/5', 'Rev3 Fedora 12 tryserver opt test mochitests-2/5', 'Rev3 Fedora 12 tryserver opt test mochitests-3/5', 'Rev3 Fedora 12 tryserver opt test mochitests-4/5', 'Rev3 Fedora 12 tryserver opt test mochitests-5/5', 'Rev3 Fedora 12 tryserver opt test mochitest-other', 'Rev3 Fedora 12 tryserver opt test reftest', 'Rev3 Fedora 12 tryserver opt test crashtest', 'Rev3 Fedora 12 tryserver opt test xpcshell', 'Rev3 Fedora 12 tryserver opt test jsreftest', 'Rev3 Fedora 12 tryserver debug test mochitests-1/5', 'Rev3 Fedora 12 tryserver debug test mochitests-2/5', 'Rev3 Fedora 12 tryserver debug test mochitests-3/5', 'Rev3 Fedora 12 tryserver debug test mochitests-4/5', 'Rev3 Fedora 12 tryserver debug test mochitests-5/5', 'Rev3 Fedora 12 tryserver debug test mochitest-other', 'Rev3 Fedora 12 tryserver debug test reftest', 'Rev3 Fedora 12 tryserver debug test crashtest', 'Rev3 Fedora 12 tryserver debug test xpcshell', 'Rev3 Fedora 12 tryserver debug test jsreftest', 'WINNT 5.2 tryserver debug test mochitests-1/5', 'WINNT 5.2 tryserver debug test mochitests-2/5', 'WINNT 5.2 tryserver debug test mochitests-3/5', 'WINNT 5.2 tryserver debug test mochitests-4/5', 'WINNT 5.2 tryserver debug test mochitests-5/5', 'WINNT 5.2 tryserver debug test mochitest-other', 'WINNT 5.2 tryserver debug test reftest', 'WINNT 5.2 tryserver debug test crashtest', 'WINNT 5.2 tryserver debug test xpcshell', 'WINNT 5.2 tryserver debug test jsreftest']

# Test Bad data should get opt & debug linux builds since 'junk' is not a valid input
MESSAGE_BUILD_JUNK = "try: --build junk --p linux --u none" 
RESULT_JUNK = ['Linux tryserver build', 'Linux tryserver leak test build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build']

# SPECIFYING opt/debug
MESSAGE_BUILD_D = "try: --build d --p linux --u none"
MESSAGE_BUILD_O = "try: --build o --p linux --u none"
MESSAGE_BUILD_DO = "try: --build do --p linux --u none"
MESSAGE_BUILD_OD = "try: --build od --p linux --u none"
# should result in those desktop, all mobile, no tests/talos
RESULT_D = ['Linux tryserver leak test build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build']
RESULT_O = ['Linux tryserver build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build']
RESULT_DO = ['Linux tryserver build', 'Linux tryserver leak test build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build']
RESULT_OD = ['Linux tryserver build', 'Linux tryserver leak test build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build']

# SPECIFIC PLATFORMS
MESSAGE_MAC_ONLY = "try: --build o --p macosx,macosx64 --m none --u none"
RESULT_MAC_ONLY = ['OS X 10.5.2 tryserver build', 'OS X 10.6.2 tryserver build']

# MOBILE ONLY AND MOBILE SELECT
#--p none -- this also tests what happens if you don't specify --build
MESSAGE_MOBILE_ONLY = "try: --p none --u none"
RESULT_MOBILE_ONLY = ['Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build']
MESSAGE_MOBILE_SELECT = "try: --p none --m android-r7,maemo4,maemo5-qt --u none"
RESULT_MOBILE_SELECT = ['Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 QT tryserver build']

# TEST SUITES
# test for 'all' and test for selective just opt
MESSAGE_ALL_TESTS = "try: --build o --u all"
MESSAGE_SELECT_TESTS = "try: --build o --p linux --u reftest,crashtest,mochitest-other"
RESULT_ALL_TESTS = ['OS X 10.5.2 tryserver build', 'OS X 10.6.2 tryserver build', 'WINNT 5.2 tryserver build', 'Linux x86-64 tryserver build', 'Linux tryserver build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-1/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-2/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-3/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-4/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitests-5/5', 'Rev3 WINNT 6.1 x64 tryserver opt test mochitest-other', 'Rev3 WINNT 6.1 x64 tryserver opt test reftest', 'Rev3 WINNT 6.1 x64 tryserver opt test crashtest', 'Rev3 WINNT 6.1 x64 tryserver opt test xpcshell', 'Rev3 WINNT 6.1 x64 tryserver opt test jsreftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-1/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-2/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-3/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-4/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitests-5/5', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test mochitest-other', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test reftest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test crashtest', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test xpcshell', 'Rev3 MacOSX Leopard 10.5.8 tryserver opt test jsreftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-1/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-2/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-3/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-4/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitests-5/5', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test mochitest-other', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test reftest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test crashtest', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test xpcshell', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver opt test jsreftest', 'Rev3 WINNT 6.1 tryserver opt test mochitests-1/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-2/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-3/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-4/5', 'Rev3 WINNT 6.1 tryserver opt test mochitests-5/5', 'Rev3 WINNT 6.1 tryserver opt test mochitest-other', 'Rev3 WINNT 6.1 tryserver opt test reftest', 'Rev3 WINNT 6.1 tryserver opt test reftest-d2d', 'Rev3 WINNT 6.1 tryserver opt test crashtest', 'Rev3 WINNT 6.1 tryserver opt test xpcshell', 'Rev3 WINNT 6.1 tryserver opt test jsreftest', 'Rev3 Fedora 12x64 tryserver opt test mochitests-1/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-2/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-3/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-4/5', 'Rev3 Fedora 12x64 tryserver opt test mochitests-5/5', 'Rev3 Fedora 12x64 tryserver opt test mochitest-other', 'Rev3 Fedora 12x64 tryserver opt test reftest', 'Rev3 Fedora 12x64 tryserver opt test crashtest', 'Rev3 Fedora 12x64 tryserver opt test xpcshell', 'Rev3 Fedora 12x64 tryserver opt test jsreftest', 'Rev3 Fedora 12 tryserver opt test mochitests-1/5', 'Rev3 Fedora 12 tryserver opt test mochitests-2/5', 'Rev3 Fedora 12 tryserver opt test mochitests-3/5', 'Rev3 Fedora 12 tryserver opt test mochitests-4/5', 'Rev3 Fedora 12 tryserver opt test mochitests-5/5', 'Rev3 Fedora 12 tryserver opt test mochitest-other', 'Rev3 Fedora 12 tryserver opt test reftest', 'Rev3 Fedora 12 tryserver opt test crashtest', 'Rev3 Fedora 12 tryserver opt test xpcshell', 'Rev3 Fedora 12 tryserver opt test jsreftest']
RESULT_SELECT_TESTS = ['Linux tryserver build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build', 'Rev3 Fedora 12 tryserver opt test reftest', 'Rev3 Fedora 12 tryserver opt test crashtest', 'Rev3 Fedora 12 tryserver opt test mochitest-other']

# TALOS SUITES
# test for 'all' and test for selection
MESSAGE_ALL_TALOS = "try: --build o --u none --t all"
MESSAGE_SELECT_TALOS = "try: --build o --p linux --m none --u none --t scroll,dromaeo,tp4"
RESULT_ALL_TALOS = ['OS X 10.5.2 tryserver build', 'OS X 10.6.2 tryserver build', 'WINNT 5.2 tryserver build', 'Linux x86-64 tryserver build', 'Linux tryserver build',  'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build','Rev3 WINNT 6.1 x64 tryserver talos nochrome', 'Rev3 WINNT 6.1 x64 tryserver talos dromaeo', 'Rev3 WINNT 6.1 x64 tryserver talos a11y', 'Rev3 WINNT 6.1 x64 tryserver talos svg', 'Rev3 WINNT 6.1 x64 tryserver talos chrome', 'Rev3 WINNT 6.1 x64 tryserver talos tp4', 'Rev3 WINNT 6.1 x64 tryserver talos dirty', 'Rev3 WINNT 6.1 x64 tryserver talos scroll', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos nochrome', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos cold', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos dromaeo', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos svg', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos chrome', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos tp4', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos dirty', 'Rev3 MacOSX Leopard 10.5.8 tryserver talos scroll', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos nochrome', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos cold', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos dromaeo', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos svg', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos chrome', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos tp4', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos dirty', 'Rev3 MacOSX Snow Leopard 10.6.2 tryserver talos scroll', 'Rev3 WINNT 5.1 tryserver talos nochrome', 'Rev3 WINNT 5.1 tryserver talos dromaeo', 'Rev3 WINNT 5.1 tryserver talos a11y', 'Rev3 WINNT 5.1 tryserver talos svg', 'Rev3 WINNT 5.1 tryserver talos chrome', 'Rev3 WINNT 5.1 tryserver talos tp4', 'Rev3 WINNT 5.1 tryserver talos dirty', 'Rev3 WINNT 5.1 tryserver talos scroll', 'Rev3 WINNT 6.1 tryserver talos nochrome', 'Rev3 WINNT 6.1 tryserver talos dromaeo', 'Rev3 WINNT 6.1 tryserver talos a11y', 'Rev3 WINNT 6.1 tryserver talos svg', 'Rev3 WINNT 6.1 tryserver talos chrome', 'Rev3 WINNT 6.1 tryserver talos tp4', 'Rev3 WINNT 6.1 tryserver talos dirty', 'Rev3 WINNT 6.1 tryserver talos scroll', 'Rev3 Fedora 12x64 tryserver talos nochrome', 'Rev3 Fedora 12x64 tryserver talos cold', 'Rev3 Fedora 12x64 tryserver talos dromaeo', 'Rev3 Fedora 12x64 tryserver talos a11y', 'Rev3 Fedora 12x64 tryserver talos svg', 'Rev3 Fedora 12x64 tryserver talos chrome', 'Rev3 Fedora 12x64 tryserver talos tp4', 'Rev3 Fedora 12x64 tryserver talos dirty', 'Rev3 Fedora 12x64 tryserver talos scroll', 'Rev3 Fedora 12 tryserver talos nochrome', 'Rev3 Fedora 12 tryserver talos cold', 'Rev3 Fedora 12 tryserver talos dromaeo', 'Rev3 Fedora 12 tryserver talos a11y', 'Rev3 Fedora 12 tryserver talos svg', 'Rev3 Fedora 12 tryserver talos chrome', 'Rev3 Fedora 12 tryserver talos tp4', 'Rev3 Fedora 12 tryserver talos dirty', 'Rev3 Fedora 12 tryserver talos scroll']
RESULT_SELECT_TALOS = ['Linux tryserver build', 'Rev3 Fedora 12 tryserver talos scroll', 'Rev3 Fedora 12 tryserver talos dromaeo', 'Rev3 Fedora 12 tryserver talos tp4']

# TESTS AND TALOS BOTH BUILD TYPES -- note we do not have debug win32 tests yet on test-masters
MESSAGE_TEST_AND_TALOS = "try: --build do --p win32 --u reftest --t nochrome,dirty"
RESULT_TEST_AND_TALOS = ['WINNT 5.2 tryserver build', 'WINNT 5.2 tryserver leak test build', 'Android R7 tryserver build', 'Maemo 4 tryserver build', 'Maemo 5 GTK tryserver build', 'Maemo 5 QT tryserver build', 'Rev3 WINNT 6.1 tryserver opt test reftest', 'WINNT 5.2 tryserver debug test reftest', 'Rev3 WINNT 5.1 tryserver talos nochrome', 'Rev3 WINNT 6.1 tryserver talos nochrome', 'Rev3 WINNT 5.1 tryserver talos dirty','Rev3 WINNT 6.1 tryserver talos dirty']

class TestTryParser(unittest.TestCase):

    def setUp(self):
        self.builderNames = DESKTOP_BUILDERS + MOBILE_BUILDERS + TEST_BUILDERS + TALOS_BUILDERS

    def test_DefaultSet(self):
        print "Testing the default set with blank input"
        self.customBuilders = TryParser(MESSAGE_DEFAULT1, self.builderNames)
        for c in self.customBuilders:
            if c not in RESULT_DEFAULT:
                print "Missed a builder in MESSAGE_DEFAULT1"

        print "Testing the default set with junk input"
        self.customBuilders = TryParser(MESSAGE_DEFAULT2, self.builderNames)
        for c in self.customBuilders:
            if c not in RESULT_DEFAULT:
                print "Missed a builder in MESSAGE_DEFAULT2"

        print "Testing the default set with junk input for --build"
        self.customBuilders = TryParser(MESSAGE_BUILD_JUNK, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_JUNK)

    def test_BuildType(self):
        print "Testing build type selection: Debug only"
        self.customBuilders = TryParser(MESSAGE_BUILD_D, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_D)
        print "Testing build type selection: Opt only"
        self.customBuilders = TryParser(MESSAGE_BUILD_O, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_O)
        print "Testing build type selection: Both (DO)"
        self.customBuilders = TryParser(MESSAGE_BUILD_DO, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_DO)
        print "Testing build type selection: Both (OD)"
        self.customBuilders = TryParser(MESSAGE_BUILD_OD, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_OD)

    def test_SpecificPlatform(self):
        print "Testing a specific platform: Mac Only"
        self.customBuilders = TryParser(MESSAGE_MAC_ONLY, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_MAC_ONLY)

    def test_MobileOnly(self):
        print "Testing Mobile Only"
        self.customBuilders = TryParser(MESSAGE_MOBILE_ONLY, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_MOBILE_ONLY)

    def test_MobileSelect(self):
        print "Testing selective Mobile platforms"
        self.customBuilders = TryParser(MESSAGE_MOBILE_SELECT, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_MOBILE_SELECT)

    def test_AllTests(self):
        print "Testing all tests"
        self.customBuilders = TryParser(MESSAGE_ALL_TESTS, self.builderNames)
        # Too many to put in the right order 
        # so let's just make sure they are all present and accounted for
        for c in self.customBuilders:
            if c not in RESULT_ALL_TESTS:
                print "Missed a Test builder"

    def test_SelectTests(self):
        print "Testing select tests"
        self.customBuilders = TryParser(MESSAGE_SELECT_TESTS, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_SELECT_TESTS)

    def test_AllTalos(self):
        print "Testing all talos"
        self.customBuilders = TryParser(MESSAGE_ALL_TALOS, self.builderNames)
        # Too many to put in the right order 
        # so let's just make sure they are all present and accounted for
        for c in self.customBuilders:
            if c not in RESULT_ALL_TALOS:
                print "Missed a Talos builder"

    def test_SelectTalos(self):
        print "Testing select talos"
        self.customBuilders = TryParser(MESSAGE_SELECT_TALOS, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_SELECT_TALOS)

    def test_TestsTalosBothBuilds(self):
        print "Testing test, talos both build types"
        self.customBuilders = TryParser(MESSAGE_TEST_AND_TALOS, self.builderNames)
        self.assertEqual(self.customBuilders, RESULT_TEST_AND_TALOS)

    def test_AllOverride(self):
        print "Testing --all override flag"
        self.customBuilders = TryParser(MESSAGE_ALL_OVERRIDE, self.builderNames)
        # Too many to put in the right order 
        # so let's just make sure they are all present and accounted for
        for c in self.customBuilders:
            if c not in RESULT_ALL_OVERRIDE:
                print "Missed a builder in the --all test"

if __name__ == '__main__':
    unittest.main()