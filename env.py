# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla-specific Buildbot steps.
#
# The Initial Developer of the Original Code is
# Mozilla Corporation.
# Portions created by the Initial Developer are Copyright (C) 2007
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Ben Hearsum <bhearsum@mozilla.com>
#   Rob Campbell <rcampbell@mozilla.com>
#   Chris Cooper <ccooper@mozilla.com>
# ***** END LICENSE BLOCK *****

MozillaEnvironments = {}

MozillaEnvironments['win32-ref-platform'] = {
    "MOZ_TOOLS": 'd:\\mozilla-build\\moztools',
    "VSINSTALLDIR": 'D:\\msvs8',
    "VS80COMMTOOLS": 'D:\\msvs8\\Common7\\Tools\\',
    "VCINSTALLDIR": 'D:\\msvs8\\VC',
    "FrameworkDir": 'C:\\WINDOWS\\Microsoft.NET\\Framework',
    "FrameworkSDKDir": 'D:\\msvs8\\SDK\\v2.0',
    "DevEnvDir": "D:\\msvs8\\VC\\Common7\\IDE",
    "MSVCDir": 'D:\\msvs8\\VC',
    "PATH": 'd:\\sdks\\v6.0\\bin;' + \
            'D:\\msvs8\\Common7\\IDE;' + \
            'D:\\msvs8\\VC\\bin;' + \
            'C:\\msvs8\\SDK\\bin;' + \
            'D:\\msvs8\\VC;' + \
            'D:\\msvs8\\Common7\\Tools;' + \
            'D:\\msvs8\\Common7\\Tools\\bin;' + \
            'd:\\mozilla-build\\hg;' + \
            'd:\\mozilla-build\\moztools\\bin;' + \
            'd:\\mozilla-build\\msys\\local\\bin;' + \
            'd:\\mozilla-build\\msys\\bin;' + \
            'd:\\mozilla-build\\7zip;' + \
            'd:\\mozilla-build\\upx203w;' + \
            'd:\\mozilla-build\\python25;' + \
            'd:\\mozilla-build\\blat261\\full;' + \
            'd:\\mozilla-build\\info-zip;' + \
            'd:\\mozilla-build\\wget;' + \
            'd:\\mozilla-build\\nsis-2.22;',
            'D:\\mozilla-build\\nsis-2.33u;' + \
            'd:\\sdks\\v6.0\\bin'
    "INCLUDE": 'D:\\sdks\\v6.0\\include;' + \
               'D:\\sdks\\v6.0\\include\\atl;' + \
               'D:\\msvs8\\VC\\ATLMFC\\INCLUDE;' + \
               'D:\\msvs8\\VC\\INCLUDE;' + \
               'D:\\msvs8\\VC\\PlatformSDK\\include',
    "LIB": 'D:\\sdks\\v6.0\\lib;' + \
           'D:\\msvs8\\VC\\ATLMFC\\LIB;' + \
           'D:\\msvs8\\VC\\LIB;' + \
           'D:\\msvs8\\VC\\PlatformSDK\\lib',
    "SDKDIR": 'D:\\sdks\\v6.0'
}

### unittest environments

MozillaEnvironments['linux-unittest'] = {
    "MOZ_NO_REMOTE": '1',
    "CVS_RSH": 'ssh',
    "DISPLAY": ':2',
    "NO_FAIL_ON_TEST_ERRORS": '1',
    "CCACHE_DIR": '/builds/ccache',
    "CCACHE_UMASK": '002',
}

MozillaEnvironments['linux64-unittest'] = MozillaEnvironments['linux-unittest'].copy() 

MozillaEnvironments['macosx-unittest'] = {
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "CVS_RSH": 'ssh',
    "NO_FAIL_ON_TEST_ERRORS": '1'  
}

MozillaEnvironments['macosx64-unittest'] = MozillaEnvironments['macosx-unittest'].copy() 

MozillaEnvironments['win32-unittest'] = {
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "MOZ_AIRBAG": '1',
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "VCVARS": 'D:\\msvs8\\VC\\bin\\vcvars32.bat',
    "MOZ_MSVCVERSION": '8',
    "MOZILLABUILD": 'D:\\mozilla-build',
    "MOZILLABUILDDRIVE": 'C:',
    "MOZILLABUILDPATH": '\\mozilla-build\\',
    "MOZ_TOOLS": 'D:\\mozilla-build\\moztools',
    "CVS_RSH": 'ssh',
    "NO_FAIL_ON_TEST_ERRORS": '1',
    "VSINSTALLDIR": 'D:\\msvs8',
    "VCINSTALLDIR": 'D:\\msvs8\\VC',
    "FrameworkDir": 'C:\\WINDOWS\\Microsoft.NET\\Framework',
    "FrameworkVersion": 'v2.0.50727',
    "FrameworkSDKDir": 'D:\\msvs8\\SDK\\v2.0',
    "MSVCDir": 'D:\\msvs8\\VC',
    "DevEnvDir": "D:\\msvs8\\Common7\\IDE",
    "LIBPATH": 'C:\\WINDOWS\\Microsoft.NET\\Framework\\v2.0.50727;' + \
               'D:\\msvs8\\VC\\ATLMFC\\LIB'
}

MozillaEnvironments['win64-unittest'] = {
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "CVS_RSH": 'ssh',
    "NO_FAIL_ON_TEST_ERRORS": '1',
}

### Talos environments
# platform SDK location.  we can build both from one generic template.
# modified from vc8 environment
MozillaEnvironments['win32-perf'] = {
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "CYGWINBASE": 'C:\\cygwin',
    "PATH": 'C:\\Python24;' + \
            'C:\\Python24\\Scripts;' + \
            'C:\\cygwin\\bin;' + \
            'C:\\WINDOWS\\System32;' + \
            'C:\\program files\\gnuwin32\\bin;' + \
            'C:\\WINDOWS;'
}

MozillaEnvironments['win32-perf-unittest'] = {
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "PATH": 'C:\\mozilla-build;' + \
            'C:\\mozilla-build\\msys\\bin;' + \
            'C:\\mozilla-build\\msys\\local\\bin;' + \
            'C:\\mozilla-build\\Python25;' + \
            'C:\\mozilla-build\\Python25\\Scripts;' + \
            'C:\\mozilla-build\\hg;' + \
            'C:\\mozilla-build\\7zip;' + \
            'C:\\mozilla-build\\upx203w;' + \
            'C:\\WINDOWS\\System32;' + \
            'C:\\WINDOWS;'
}

MozillaEnvironments['win64-perf'] = {
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
}

MozillaEnvironments['linux-perf'] = {
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "DISPLAY": ":0",
}

MozillaEnvironments['mac-perf'] = {
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    # for extracting dmg's
    "PAGER": '/bin/cat',
}

MozillaEnvironments['android-unittest'] = {
}

MozillaEnvironments['android-perf'] = {
}